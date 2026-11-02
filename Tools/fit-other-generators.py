#!/usr/bin/env python3
"""fit-outfits.py for outfits made by generators other than Tripo (Hunyuan3D, Pixal3D).

    python3 fit-other-generators.py <rigged-base.glb> <outfits.json> <out-dir>

Same arguments and output as fit-outfits.py. Outfits marked `"otherGenerator": true`
get four adjustments their meshes need; every other outfit is cut exactly as
fit-outfits.py cuts it.

1. Node transforms are applied. Hunyuan's GLB comes out of Blender rotated 90°
   on its node, and fit-outfits reads raw vertex positions.
2. Colors are sampled at triangle centers. Pixal3D's texture is many small UV
   islands, and samples at the corners land on neighboring islands' texels.
3. For the alignment only, the outfit's skin tone is evened out to the base's.
   Alignment pairs up skin by color, and other generators shift the tone.
4. Each item's color is taken from the outer surface. Pixal3D also builds the
   body under the clothes, and sampling both layers lands between the two.
   Tops are sampled at the front of the chest, clear of a ponytail. An outfit with a
   single garment (a designer piece over the base's basics) skips colors altogether:
   the garment is whatever differs from the base and isn't skin, so prints work.
5. Loose fragments are dropped from each item, and hair pieces on the face: the
   generator's own brows and eyes would otherwise ride along with the hair.
6. Item textures keep 2048 px, with each UV island's colors spread past its
   edge first. Pixal3D's atlas is 4096 px of small islands; shrinking it
   unpadded blends every island with its neighbors (streaks of skin).
"""

import contextlib
import importlib.util
import io
import json
import os
import sys

import numpy as np
from PIL import Image

np.seterr(all="ignore")
tools = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("fit", os.path.join(tools, "fit-outfits.py"))
fit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fit)
rig = fit.rig
tripo_mesh, tripo_align, tripo_split, tripo_write = fit.Mesh, fit.align, fit.split_items, fit.write_item


# MARK: - Reading other generators' meshes

def node_matrix(node):
    if "matrix" in node:
        return np.array(node["matrix"], np.float64).reshape(4, 4).T
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    rotation = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    matrix = np.eye(4)
    matrix[:3, :3] = rotation * np.array(node.get("scale", [1, 1, 1]))
    matrix[:3, 3] = node.get("translation", [0, 0, 0])
    return matrix


def center_colors(mesh):
    """Base color per vertex: the median (by brightness) of its triangles' center colors."""
    info = mesh.material["pbrMetallicRoughness"]["baseColorTexture"]
    texture = np.asarray(Image.open(io.BytesIO(mesh.texture(info))).convert("RGB"), np.float32) / 255
    th, tw, _ = texture.shape
    centers = mesh.uvs[mesh.indices].mean(axis=1)
    colors = texture[np.clip((centers[:, 1] * (th - 1)).astype(int), 0, th - 1),
                     np.clip((centers[:, 0] * (tw - 1)).astype(int), 0, tw - 1)]
    vertex = mesh.indices.ravel()
    triangle = np.repeat(np.arange(len(mesh.indices)), 3)
    order = np.lexsort((colors[triangle].sum(axis=1), vertex))
    vertex, triangle = vertex[order], triangle[order]
    starts = np.searchsorted(vertex, np.arange(len(mesh.points)))
    ends = np.searchsorted(vertex, np.arange(len(mesh.points)), side="right")
    return colors[triangle[np.clip((starts + ends) // 2, 0, len(triangle) - 1)]].astype(np.float32)


def face_color(points, colors, marks, height):
    """Median color at the front of the face, picked the way fit-outfits picks the base's."""
    rows = np.abs(points[:, 1] - marks["nose"]) < height * 0.02
    face = rows & (np.abs(points[:, 0]) < height * 0.03)
    face &= points[:, 2] > np.percentile(points[rows, 2], 90)
    return np.median(colors[face], axis=0)


# MARK: - Item colors

def outer_reference(colors, offsets):
    """Median Lab color of the outer of two color groups, or of all samples when they agree."""
    if len(colors) < 20:
        return np.median(colors, axis=0)
    lo, hi = np.percentile(colors[:, 0], [20, 80])
    centers = np.array([colors[colors[:, 0] <= lo].mean(axis=0), colors[colors[:, 0] >= hi].mean(axis=0)])
    for _ in range(10):
        group = np.argmin(np.linalg.norm(colors[:, None] - centers[None], axis=2), axis=1)
        centers = np.array([colors[group == g].mean(axis=0) if (group == g).any() else centers[g] for g in (0, 1)])
    sizes = [(group == g).sum() for g in (0, 1)]
    if np.linalg.norm(centers[0] - centers[1]) < 15 or min(sizes) < 0.15 * len(colors):
        return np.median(colors, axis=0)
    outer = int(np.argmax([np.median(offsets[group == g]) for g in (0, 1)]))
    return np.median(colors[group == outer], axis=0)


def split_other(outfit, base, marks, height, items):
    """fit-outfits' split_items, with each item's color taken from the outer surface."""
    points = outfit.aligned
    match = fit.nearest(points, base.points)
    colors, base_colors = fit.lab(outfit.colors), fit.lab(base.colors[match])
    from_base = np.linalg.norm(colors - base_colors, axis=1)
    sticks_out = np.linalg.norm(points - base.points[match], axis=1) > height * 0.02
    offsets = np.einsum("ij,ij->i", points - base.points[match], base.normals[match])
    y = points[:, 1]

    slots = fit.bands(marks, height)
    distances = np.full((len(points), len(items)), np.inf)
    outfit.references = {}
    for column, item in enumerate(items):
        band = slots[item["slot"]]
        core = (from_base > 12) & (y > band["core"][0]) & (y < band["core"][1])
        if item["slot"] == "top":
            core &= (np.abs(points[:, 0]) < height * 0.07) & (points[:, 2] > marks["center_z"])
        reference = outer_reference(colors[core], offsets[core])
        outfit.references[column] = reference
        inside = (y > band["range"][0]) & (y < band["range"][1])
        gap = np.linalg.norm(colors - reference, axis=1)
        distances[:, column] = np.where(inside & (gap < fit.COLOR_TOLERANCE[item["slot"]]), gap, np.inf)
    best = np.argmin(distances, axis=1)
    best_distance = distances[np.arange(len(points)), best]
    garment = np.isfinite(best_distance) & ((best_distance < from_base * 0.8) | sticks_out)

    head_center_z = np.median(base.points[base.points[:, 1] > marks["neck"], 2])
    face_zone = (y > marks["neck"]) & (y < marks["nose"] + height * 0.075) & (points[:, 2] > head_center_z)
    off_skin = np.linalg.norm(points - base.points[match], axis=1) > height * 0.007
    hair_columns = [c for c, item in enumerate(items) if item["slot"] == "hair"]
    garment &= ~(np.isin(best, hair_columns) & face_zone & ~off_skin)
    label = np.where(garment, best, -1)
    outfit.single = len(items) == 1 and hasattr(outfit, "skin")
    if outfit.single:
        label = single_garment(outfit, base, items[0], colors, from_base, sticks_out, face_zone, slots, marks, y)

    adjacency = rig.vertex_adjacency(outfit.indices.ravel(), points)
    for _ in range(6):
        votes = np.zeros((len(points), len(items) + 1))
        for column in range(-1, len(items)):
            votes[:, column + 1] = rig.neighbor_share(label == column, adjacency)
        label = np.argmax(votes, axis=1) - 1
    for column, item in enumerate(items):
        label = fit.fill_holes(label, column, adjacency)
        # The generator's own brows and eyes come out as hair fragments on the face.
        label = drop_fragments(label, column, adjacency, area=face_zone if item["slot"] == "hair" else None)

    outfit.adjacency = adjacency
    outfit.labels = label
    for column, item in enumerate(items):
        print(f"  {item['id']}: {int((label == column).sum())} vertices")
    return label


def drop_fragments(label, column, adjacency, keep=0.03, area=None):
    """Unlabels the item's connected parts smaller than `keep` of its largest part, and
    parts mostly inside `area` (a vertex mask) when given."""
    neighbors, starts, welded = adjacency
    ids = welded.max() + 1
    mine = np.zeros(ids, bool)
    mine[welded[label == column]] = True
    if not mine.any():
        return label
    sources = np.repeat(np.arange(ids), np.diff(starts))
    inside = mine[sources] & mine[neighbors]
    a, b = sources[inside], neighbors[inside]
    # Connected parts: spread the smallest id along edges, with pointer jumping to converge fast.
    part = np.arange(ids)
    while True:
        before = part.copy()
        np.minimum.at(part, a, part[b])
        for _ in range(4):
            part = part[part]
        if np.array_equal(part, before):
            break
    names, sizes = np.unique(part[mine], return_counts=True)
    dropped = names[sizes < sizes.max() * keep]
    if area is not None:
        in_area = np.zeros(ids)
        np.add.at(in_area, welded[area & (label == column)], 1)
        share = np.bincount(part[mine], weights=(in_area > 0)[mine], minlength=ids)[names] / sizes
        dropped = np.union1d(dropped, names[share > 0.5])
    out = label.copy()
    out[(np.isin(part, dropped) & mine)[welded] & (label == column)] = -1
    return out


# MARK: - Writing

ITEM_TEXTURE_SIZE = 2048


def island_texels(uvs, triangles, h, w):
    """Texels the triangles cover, sampled on a grid inside each (its triangles are small)."""
    covered = np.zeros((h, w), bool)
    corners = uvs[triangles] * [w - 1, h - 1]
    steps = np.linspace(0, 1, 7)
    for s in steps:
        for t in steps[steps <= 1 - s + 1e-9]:
            point = corners[:, 0] * (1 - s - t) + corners[:, 1] * s + corners[:, 2] * t
            covered[np.clip(np.round(point[:, 1]).astype(int), 0, h - 1),
                    np.clip(np.round(point[:, 0]).astype(int), 0, w - 1)] = True
    return covered


def padded(image, uvs, triangles):
    """The texture with every texel outside the item's UV islands set to the nearest
    islands' colors (push-pull), so shrinking it doesn't blend in other parts of the
    outfit or skin."""
    texture = np.asarray(image).astype(np.float32)
    h, w, _ = texture.shape
    covered = island_texels(uvs, triangles, h, w)
    if h % 2 or w % 2:
        return image
    # Push: sums of known colors and counts, halving until a side is odd.
    levels = [(np.where(covered[..., None], texture, 0), covered.astype(np.float32))]
    while all(side % 2 == 0 and side > 1 for side in levels[-1][1].shape):
        color, known = levels[-1]
        lh, lw = known.shape
        levels.append((color.reshape(lh // 2, 2, lw // 2, 2, 3).sum(axis=(1, 3)),
                       known.reshape(lh // 2, 2, lw // 2, 2).sum(axis=(1, 3))))
    # Pull: each level keeps its own average where it has known texels, else the coarser one.
    color, known = levels[-1]
    average = color / np.maximum(known, 1e-6)[..., None]
    for color, known in reversed(levels[:-1]):
        coarser = np.repeat(np.repeat(average, 2, axis=0), 2, axis=1)
        average = np.where((known > 0)[..., None], color / np.maximum(known, 1e-6)[..., None], coarser)
    filled = np.where(covered[..., None], texture, average)
    return Image.fromarray(np.clip(filled, 0, 255).astype(np.uint8))


def write_other_item(path, outfit, base, skeleton_names, mask, item, height, reference, skin):
    """fit-outfits' write_item with padded, 2048 px base color textures."""
    all_points, all_normals, all_uvs, triangles = fit.clip_item(outfit, mask, item)
    used = np.unique(triangles)
    remap = np.full(len(all_points), -1)
    remap[used] = np.arange(len(used))
    points = all_points[used]
    normals = all_normals[used]
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)

    match = fit.nearest(points, base.points)
    points = fit.push_outside(points, remap[triangles], base, match, height * fit.PUSH_MARGIN[item["slot"]])
    joints, weights = fit.transfer_weights(points, remap[triangles], base, len(skeleton_names))
    if item["slot"] == "hair":
        joints[:] = [skeleton_names.index("Head"), 0, 0, 0]
        weights[:] = [1, 0, 0, 0]

    writer = rig.GlbWriter()
    pbr = outfit.material.get("pbrMetallicRoughness", {})
    images, textures = [], []
    for info, size in ((pbr.get("baseColorTexture"), ITEM_TEXTURE_SIZE), (outfit.material.get("normalTexture"), 512)):
        if info:
            image = Image.open(io.BytesIO(outfit.texture(info))).convert("RGB")
            if info is pbr.get("baseColorTexture"):
                # A garment made on its own (fit-garments.py) has no painted skin to clean.
                if not getattr(outfit, "garment", None):
                    # A designer garment may be a print: see clean_edges.
                    own = island_texels(all_uvs, triangles, image.height, image.width) if outfit.single else None
                    image = fit.clean_edges(image, all_points, all_uvs, triangles, reference, skin, item, own=own)
                image = padded(image, all_uvs, triangles)
            image.thumbnail((size, size), Image.LANCZOS)
            out = io.BytesIO()
            image.save(out, "JPEG", quality=88)
            images.append({"bufferView": writer.view(out.getvalue()), "mimeType": "image/jpeg"})
            textures.append({"source": len(images) - 1, "sampler": 0})
    material = {"name": f"MI_{item['id']}", "doubleSided": True,
                "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0, "roughnessFactor": 1}}
    if len(textures) > 1:
        material["normalTexture"] = {"index": 1}

    attributes = {
        "POSITION": writer.accessor(points.astype(np.float32), "VEC3", 5126, 34962, bounds=True),
        "NORMAL": writer.accessor(normals.astype(np.float32), "VEC3", 5126, 34962),
        "TEXCOORD_0": writer.accessor(all_uvs[used], "VEC2", 5126, 34962),
        "JOINTS_0": writer.accessor(joints.astype(np.uint8), "VEC4", 5121, 34962),
        "WEIGHTS_0": writer.accessor(weights.astype(np.float32), "VEC4", 5126, 34962),
    }
    index_type, component = (np.uint16, 5123) if len(used) < 65536 else (np.uint32, 5125)
    indices = writer.accessor(remap[triangles].ravel().astype(index_type), "SCALAR", component, 34963)
    gltf = fit.skeleton_document(base, writer, item["id"], attributes, indices, material, textures, images)
    writer.write(path, gltf)
    print(f"  wrote {os.path.basename(path)}: {len(used)} vertices, {len(triangles)} triangles")
    return points, normals


# Lab distances: from the base's color at the same spot, and from the outfit's own skin.
GARMENT_FROM_BASE = 14
GARMENT_FROM_SKIN = 14
GARMENT_FROM_BASICS = 14


def base_colors_at(outfit, base):
    """The base's color at the nearest base point to each outfit vertex."""
    return base.colors[fit.nearest(outfit.aligned, base.points)]


def color_nearest(colors, target):
    """Median Lab color of the larger-than-15% group (2-means) closest to target, or of all."""
    if len(colors) < 20:
        return np.median(colors, axis=0)
    lo, hi = np.percentile(colors[:, 0], [20, 80])
    centers = np.array([colors[colors[:, 0] <= lo].mean(axis=0), colors[colors[:, 0] >= hi].mean(axis=0)])
    for _ in range(10):
        group = np.argmin(np.linalg.norm(colors[:, None] - centers[None], axis=2), axis=1)
        centers = np.array([colors[group == g].mean(axis=0) if (group == g).any() else centers[g] for g in (0, 1)])
    groups = [g for g in (0, 1) if (group == g).sum() >= 0.15 * len(colors)]
    best = min(groups, key=lambda g: np.linalg.norm(centers[g] - target))
    return np.median(colors[group == best], axis=0)


def single_garment(outfit, base, item, colors, from_base, sticks_out, face_zone, slots, marks, y):
    """Labels for an outfit that adds one garment to the base (a designer piece): whatever
    differs from the base and isn't skin, inside the slot's heights. No color is sampled,
    so prints and several colors work. Tops reach up behind the head, for hoods."""
    low, high = slots[item["slot"]]["range"]
    if item["slot"] == "top":
        high = max(high, marks["nose"] + (marks["nose"] - marks["neck"]))
    inside = (y > low) & (y < high)
    not_skin = np.linalg.norm(colors - fit.lab(outfit.skin[None])[0], axis=1) > GARMENT_FROM_SKIN
    height = marks["neck"] / 0.8
    waist = marks["crotch"] + height * 0.1
    base_lab = fit.lab(base_colors_at(outfit, base))
    gray = np.linalg.norm(base_lab[:, 1:], axis=1) < 6
    # A top always covers the base's tank top and a bottom its shorts, so there anything
    # that isn't skin is the garment, even a white or gray one close to the basics' color.
    covers = {"top": y > waist, "bottom": y < waist}.get(item["slot"], np.zeros(len(y), bool))
    garment = inside & not_skin & ((from_base > GARMENT_FROM_BASE) | sticks_out | (covers & gray)) & ~face_zone
    # The base's own basics (gray shorts and tank top), as this generator painted them: a
    # shade off the base's gray, so a plain difference would keep them. They're sampled where
    # the base is gray but this slot's garment doesn't go (the shorts' legs under a top, the
    # tank top over a bottom), keeping the color group closest to the base's gray, and only
    # removed on that side of the waist, so the garment's own colors elsewhere are never touched.
    sides = {"top": ((marks["crotch"] - height * 0.09, marks["crotch"] - height * 0.03), y < waist),
             "bottom": ((marks["armpit"] - height * 0.06, marks["armpit"] - height * 0.01), y > waist)}
    if item["slot"] in sides:
        (low, high), side = sides[item["slot"]]
        sample = (y > low) & (y < high) & gray & not_skin
        if sample.sum() > 50:
            basics = color_nearest(colors[sample], np.median(base_lab[sample], axis=0))
            garment &= ~(side & (np.linalg.norm(colors - basics, axis=1) < GARMENT_FROM_BASICS))
    print(f"  {item['id']}: one garment, {int(garment.sum())} vertices differ from the base")
    return np.where(garment, 0, -1)


def best_forward(outfit, base, skin_color, height):
    """The facing (fit-outfits' TURN keys) under which the outfit's skin lines up best with the base's."""
    target = base.points[np.linalg.norm(base.colors - skin_color, axis=1) < 0.12]
    errors = {}
    for forward in fit.TURN:
        outfit.forward = forward
        with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet):
            tripo_align(outfit, base, skin_color, height)
        skin = outfit.aligned[np.linalg.norm(outfit.colors - skin_color, axis=1) < 0.12]
        errors[forward] = float(np.median(np.linalg.norm(skin - target[fit.nearest(skin, target)], axis=1))) / height
    best = min(errors, key=errors.get)
    print(f"  facing {best} (alignment: " + ", ".join(f"{f} {e:.4f}" for f, e in errors.items()) + ")")
    return best


# MARK: - Hooking into fit-outfits

def install(base_path, outfits_path):
    """Swaps fit-outfits' Mesh, align and split_items for versions that adjust marked outfits."""
    base = tripo_mesh(base_path)
    height = float(np.ptp(base.points[:, 1]))
    marks = rig.measure(rig.surface_samples(base.points, base.indices.ravel(), 300_000), height)
    base_skin = face_color(base.points, base.colors, marks, height)
    others = {s["model"]: s for s in json.load(open(outfits_path)) if s.get("otherGenerator")}

    class Mesh(tripo_mesh):
        def __init__(self, path):
            super().__init__(path)
            self.other_generator = path in others
            if not self.other_generator:
                return
            matrix = node_matrix(next(n for n in self.gltf["nodes"] if n.get("mesh") == 0))
            self.points = self.points @ matrix[:3, :3].T + matrix[:3, 3]
            normals = self.normals @ np.linalg.inv(matrix[:3, :3])
            self.normals = normals / np.linalg.norm(normals, axis=1, keepdims=True)
            self.colors = center_colors(self)
            # The rough placement align() starts from, to find this outfit's own face.
            forward = others[path].get("forward", "+z")
            points = self.points @ fit.TURN.get(forward, np.eye(3)).T
            points *= height / np.ptp(points[:, 1])
            points[:, 1] -= points[:, 1].min()
            points[:, [0, 2]] -= np.median(points[points[:, 1] < height * 0.5][:, [0, 2]], axis=0)
            if forward == "auto":
                # Facing unknown yet: the base is bald, so the whole head at nose height is skin.
                skin = np.median(self.colors[np.abs(points[:, 1] - marks["nose"]) < height * 0.02], axis=0)
            else:
                skin = face_color(points, self.colors, marks, height)
            self.skin = skin
            self.alignment_colors = np.clip(self.colors * base_skin / np.maximum(skin, 1e-3), 0, 1)

    def align(outfit, base, skin_color, height):
        colors = outfit.colors
        outfit.colors = getattr(outfit, "alignment_colors", colors)
        try:
            if outfit.forward == "auto":
                outfit.forward = best_forward(outfit, base, skin_color, height)
            tripo_align(outfit, base, skin_color, height)
        finally:
            outfit.colors = colors

    def split_items(outfit, base, marks, height, items):
        split = split_other if outfit.other_generator else tripo_split
        return split(outfit, base, marks, height, items)

    def write_item(path, outfit, *rest):
        write = write_other_item if outfit.other_generator else tripo_write
        return write(path, outfit, *rest)

    fit.Mesh, fit.align, fit.split_items, fit.write_item = Mesh, align, split_items, write_item


def main():
    base_path, outfits_path, out_dir = sys.argv[1:4]
    install(base_path, outfits_path)
    fit.main()


if __name__ == "__main__":
    main()
