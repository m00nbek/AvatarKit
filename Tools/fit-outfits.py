#!/usr/bin/env python3
"""Cuts generated outfits into items that fit the rigged base body.

    python3 fit-outfits.py <rigged-base.glb> <outfits.json> <out-dir>

Each outfit is the base character, generated again wearing clothes in the same
pose. Lined up with the base, anything whose color differs from the base at the
same spot is clothing or hair; height bands and colors split it into the
outfit's items. Items take the base's bone weights at the nearest point, so
they move with the body underneath.

The base is written back split into patches by which items cover them, named
`<mesh>__covers__<item>+<item>`; the app hides a patch while one of those items
is worn.

outfits.json: [{"model": path, "forward": "+x", "items": [{"id", "slot"}]}]
"""

import importlib.util
import io
import json
import os
import sys

import numpy as np
from PIL import Image

np.seterr(all="ignore")
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("rig", os.path.join(here, "rig-generated-body.py"))
rig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rig)

TURN = {"+z": np.eye(3), "-z": np.diag([-1, 1, -1]),
        "+x": np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]]), "-x": np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]])}


# MARK: - Loading

class Mesh:
    def __init__(self, path):
        self.gltf, self.binary = rig.read_glb(path)
        primitive = self.gltf["meshes"][0]["primitives"][0]
        attributes = primitive["attributes"]
        self.points = rig.accessor(self.gltf, self.binary, attributes["POSITION"]).astype(np.float64)
        self.normals = rig.accessor(self.gltf, self.binary, attributes["NORMAL"]).astype(np.float64)
        self.uvs = rig.accessor(self.gltf, self.binary, attributes["TEXCOORD_0"]).astype(np.float32)
        self.indices = rig.accessor(self.gltf, self.binary, primitive["indices"]).astype(np.int64).reshape(-1, 3)
        self.joints = rig.accessor(self.gltf, self.binary, attributes["JOINTS_0"]) if "JOINTS_0" in attributes else None
        self.weights = rig.accessor(self.gltf, self.binary, attributes["WEIGHTS_0"]) if "WEIGHTS_0" in attributes else None
        material = self.gltf["materials"][primitive["material"]]
        self.material = material
        self.colors = rig.vertex_colors(self.gltf, self.binary, primitive, self.uvs)

    def texture(self, info):
        source = self.gltf["textures"][info["index"]]["source"]
        return rig.image_bytes(self.gltf, self.binary, source)


def nearest(queries, targets, chunk=1500):
    """Index of the nearest target for each query (brute force, chunked)."""
    targets32 = targets.astype(np.float32)
    squared = (targets32 ** 2).sum(axis=1)
    result = np.empty(len(queries), np.int64)
    for start in range(0, len(queries), chunk):
        q = queries[start:start + chunk].astype(np.float32)
        d = squared[None, :] - 2 * q @ targets32.T
        result[start:start + chunk] = np.argmin(d, axis=1)
    return result


# MARK: - Alignment

def similarity(source, target):
    """Scale, rotation and translation taking source points onto target (Umeyama)."""
    mu_s, mu_t = source.mean(axis=0), target.mean(axis=0)
    s, t = source - mu_s, target - mu_t
    u, d, vt = np.linalg.svd(t.T @ s / len(source))
    fix = np.diag([1, 1, np.sign(np.linalg.det(u @ vt))])
    rotation = u @ fix @ vt
    scale = np.trace(np.diag(d) @ fix) / (s ** 2).sum(axis=1).mean()
    return scale, rotation, mu_t - scale * rotation @ mu_s


def align(outfit, base, skin_color, height):
    """Lines the outfit up with the base using the skin both share (face, arms)."""
    points = outfit.points @ TURN[outfit.forward].T
    points *= height / np.ptp(points[:, 1])
    points[:, 1] -= points[:, 1].min()
    points[:, [0, 2]] -= np.median(points[points[:, 1] < height * 0.5][:, [0, 2]], axis=0)

    source_mask = np.linalg.norm(outfit.colors - skin_color, axis=1) < 0.12
    target_mask = np.linalg.norm(base.colors - skin_color, axis=1) < 0.12
    rng = np.random.default_rng(2)
    source_index = rng.choice(np.where(source_mask)[0], min(5000, source_mask.sum()), replace=False)
    target_points = base.points[target_mask]

    for _ in range(25):
        moved = points[source_index]
        matches = target_points[nearest(moved, target_points)]
        distance = np.linalg.norm(moved - matches, axis=1)
        keep = distance < np.percentile(distance, 80)
        scale, rotation, offset = similarity(moved[keep], matches[keep])
        points = scale * points @ rotation.T + offset
    error = np.median(np.linalg.norm(points[source_index] - target_points[nearest(points[source_index], target_points)], axis=1))
    print(f"  aligned: median skin distance {error / height:.4f} of height")
    outfit.aligned = points
    outfit.aligned_normals = outfit.normals @ TURN[outfit.forward].T


# MARK: - Splitting into items

# How far (CIE76 delta E) a vertex's color may be from the item's sampled color.
# Generated textures shade folds darker, so clothes get more room than hair.
COLOR_TOLERANCE = {"hair": 30, "top": 28, "bottom": 28, "shoes": 28}


def lab(rgb):
    """sRGB (0...1) to CIE L*a*b*, where distances match how different colors look."""
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = linear @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]).T
    xyz /= [0.95047, 1.0, 1.08883]
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1)


def bands(marks, height):
    """Where each slot's item can be, and where its color is sampled (y ranges)."""
    waist = marks["crotch"] + height * 0.1
    return {
        "hair": {"range": (marks["neck"] - height * 0.18, 9), "core": (height * 0.96, 9)},
        "top": {"range": (marks["crotch"] - height * 0.05, marks["neck"] + height * 0.03),
                "core": (marks["armpit"] - height * 0.04, marks["armpit"] + height * 0.02)},
        "bottom": {"range": (0, waist + height * 0.06), "core": (height * 0.2, height * 0.3)},
        "shoes": {"range": (-1, height * 0.12), "core": (-1, height * 0.025)},
    }


def split_items(outfit, base, marks, height, items):
    """Labels each outfit vertex with an item, or -1 for the body. A vertex belongs to an
    item when its color is clearly closer to that item's than to the base's color at the
    same spot (so blue jeans over gray shorts count, the face doesn't)."""
    points = outfit.aligned
    match = nearest(points, base.points)
    colors, base_colors = lab(outfit.colors), lab(base.colors[match])
    from_base = np.linalg.norm(colors - base_colors, axis=1)
    sticks_out = np.linalg.norm(points - base.points[match], axis=1) > height * 0.02
    y = points[:, 1]

    slots = bands(marks, height)
    distances = np.full((len(points), len(items)), np.inf)
    outfit.references = {}
    for column, item in enumerate(items):
        band = slots[item["slot"]]
        core = (from_base > 12) & (y > band["core"][0]) & (y < band["core"][1])
        if item["slot"] == "top":
            core &= np.abs(points[:, 0]) < height * 0.07
        reference = np.median(colors[core], axis=0)
        outfit.references[column] = reference
        inside = (y > band["range"][0]) & (y < band["range"][1])
        gap = np.linalg.norm(colors - reference, axis=1)
        distances[:, column] = np.where(inside & (gap < COLOR_TOLERANCE[item["slot"]]), gap, np.inf)
    best = np.argmin(distances, axis=1)
    best_distance = distances[np.arange(len(points)), best]
    garment = np.isfinite(best_distance) & ((best_distance < from_base * 0.8) | sticks_out)

    # On the face, dark eyes, lashes and brows can match dark hair. Hair there (bangs)
    # stands off the skin; painted features don't.
    head_center_z = np.median(base.points[base.points[:, 1] > marks["neck"], 2])
    face_zone = (y > marks["neck"]) & (y < marks["nose"] + height * 0.075) & (points[:, 2] > head_center_z)
    off_skin = np.linalg.norm(points - base.points[match], axis=1) > height * 0.007
    hair_columns = [c for c, item in enumerate(items) if item["slot"] == "hair"]
    on_face = np.isin(best, hair_columns) & face_zone & ~off_skin
    garment &= ~on_face
    label = np.where(garment, best, -1)

    # Smooth labels over the surface so stray vertices join their neighbors.
    adjacency = rig.vertex_adjacency(outfit.indices.ravel(), points)
    for _ in range(6):
        votes = np.zeros((len(points), len(items) + 1))
        for column in range(-1, len(items)):
            votes[:, column + 1] = rig.neighbor_share(label == column, adjacency)
        label = np.argmax(votes, axis=1) - 1
    for column in range(len(items)):
        label = fill_holes(label, column, adjacency)

    outfit.adjacency = adjacency
    outfit.labels = label
    for column, item in enumerate(items):
        print(f"  {item['id']}: {int((label == column).sum())} vertices")
    return label


def fill_holes(label, column, adjacency, largest=0.03):
    """Gives an item the small patches of body it surrounds completely: spots of skin
    color painted on a shoe or a tee would otherwise be holes showing the body."""
    neighbors, starts, welded = adjacency
    ids = welded.max() + 1
    mine = np.zeros(ids, bool)
    mine[welded[label == column]] = True
    other_item = np.zeros(ids, bool)
    other_item[welded[(label != column) & (label >= 0)]] = True
    sources = np.repeat(np.arange(ids), np.diff(starts))
    keep = ~mine[sources] & ~mine[neighbors]
    sources, targets = sources[keep], neighbors[keep]
    # Connected parts of the rest of the surface, by spreading the smallest id.
    component = np.where(mine, -1, np.arange(ids))
    for _ in range(500):
        spread = component.copy()
        np.minimum.at(spread, sources, component[targets])
        if np.array_equal(spread, component):
            break
        component = spread
    parts, sizes = np.unique(component[~mine], return_counts=True)
    touches_other = np.zeros(ids, bool)
    touches_other[component[other_item]] = True
    small = parts[(sizes < mine.sum() * largest) & ~touches_other[parts]]
    filled = np.isin(component, small) & ~mine
    out = label.copy()
    out[filled[welded]] = column
    return out


# MARK: - Writing

# Passes of smoothing on an item's 0/1 mask before cutting at its 0.5 line. Tripo
# paints collars and boot tops with a ragged fringe; enough passes cut straight
# through it, and clean_edges repaints what's left.
CUT_SMOOTHING = {"hair": 4, "top": 16, "bottom": 12, "shoes": 12}


def clip_item(outfit, mask, item):
    """The item's part of the outfit mesh, cut along a smooth line instead of along
    whole triangles (which leaves a sawtooth edge): each vertex gets a smoothed
    in-or-out value and triangles crossing 0.5 are clipped there."""
    value = mask.astype(np.float32)
    for _ in range(CUT_SMOOTHING[item["slot"]]):
        value = 0.5 * value + 0.5 * rig.neighbor_share(value, outfit.adjacency)
    inside = value >= 0.5
    points, normals, uvs = [outfit.aligned], [outfit.aligned_normals], [outfit.uvs]
    count = len(outfit.aligned)
    cuts = {}

    def cut(a, b):
        key = (min(a, b), max(a, b))
        if key not in cuts:
            a, b = key
            t = (0.5 - value[a]) / (value[b] - value[a])
            points.append(outfit.aligned[a] + t * (outfit.aligned[b] - outfit.aligned[a]))
            normals.append(outfit.aligned_normals[a] + t * (outfit.aligned_normals[b] - outfit.aligned_normals[a]))
            uvs.append(outfit.uvs[a] + t * (outfit.uvs[b] - outfit.uvs[a]))
            cuts[key] = count + len(cuts)
        return cuts[key]

    flags = inside[outfit.indices]
    triangles = [outfit.indices[flags.all(axis=1)]]
    clipped = []
    for tri in outfit.indices[flags.any(axis=1) & ~flags.all(axis=1)]:
        polygon = []
        for i in range(3):
            a, b = tri[i], tri[(i + 1) % 3]
            if inside[a]:
                polygon.append(a)
            if inside[a] != inside[b]:
                polygon.append(cut(a, b))
        clipped += [[polygon[0], polygon[i], polygon[i + 1]] for i in range(1, len(polygon) - 1)]
    if clipped:
        triangles.append(np.array(clipped))
    stack = lambda parts: np.concatenate([np.atleast_2d(p) for p in parts])
    return stack(points), stack(normals), stack(uvs).astype(np.float32), np.concatenate(triangles)


# How far (delta E) a texel near a cut edge may be from the item's color before
# it counts as the neighbor's paint (skin on a collar, leg on a boot).
EDGE_TOLERANCE = {"hair": 26, "top": 24, "bottom": 24, "shoes": 24}
EDGE_RINGS = {"hair": 2, "top": 6, "bottom": 5, "shoes": 5}
# Lab distance from the skin within which a print's edge texel counts as skin.
PRINT_FROM_SKIN = 18


def clean_edges(image, positions, uvs, triangles, reference, skin, item, own=None):
    """Repaints the strip along an item's cut edge. In the generated outfit the item
    and whatever touched it (neck skin, legs, scalp) were painted as one surface, so
    the item's edge triangles carry some of the neighbor's colors. Those texels are
    filled in from the item's own colors nearby.

    `own` (the texels of the item's own triangles) is for prints: with many colors in
    the item, only skin-colored texels are foreign, and fills come only from `own`, as
    an atlas of small islands puts other parts of the outfit right next to the item's."""
    texture = np.asarray(image).astype(np.float32)
    h, w, _ = texture.shape

    # Edge triangles: within a couple of rings of the item's open border (seams joined).
    _, welded = np.unique(np.round(positions / 1e-6).astype(np.int64), axis=0, return_inverse=True)
    welded = welded.ravel()
    faces = welded[triangles]
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    near = np.zeros(welded.max() + 1, bool)
    near[unique[counts == 1].ravel()] = True
    for _ in range(EDGE_RINGS[item["slot"]]):
        touching = near[faces].any(axis=1)
        near[faces[touching].ravel()] = True
    edge_triangles = triangles[near[faces].any(axis=1)]

    # Texels those triangles cover.
    area = np.zeros((h, w), bool)
    pixel = uvs * [w - 1, h - 1]
    for tri in edge_triangles:
        u, v = pixel[tri, 0], pixel[tri, 1]
        x0, x1 = max(int(u.min()) - 1, 0), min(int(u.max()) + 2, w)
        y0, y1 = max(int(v.min()) - 1, 0), min(int(v.max()) + 2, h)
        if x1 <= x0 or y1 <= y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1), np.arange(y0, y1))
        d = (v[1] - v[2]) * (u[0] - u[2]) + (u[2] - u[1]) * (v[0] - v[2])
        if abs(d) < 1e-9:
            continue
        l0 = ((v[1] - v[2]) * (xs - u[2]) + (u[2] - u[1]) * (ys - v[2])) / d
        l1 = ((v[2] - v[0]) * (xs - u[2]) + (u[0] - u[2]) * (ys - v[2])) / d
        inside = (l0 >= -0.05) & (l1 >= -0.05) & (1 - l0 - l1 >= -0.05)
        area[ys[inside], xs[inside]] = True

    colors = lab(texture.reshape(-1, 3) / 255).reshape(h, w, 3)
    from_item = np.linalg.norm(colors - reference, axis=2)
    # Far from the item's color, or nearer the skin's than the item's (a blended fringe).
    from_skin = np.linalg.norm(colors - skin, axis=2)
    if own is None:
        foreign = area & ((from_item > EDGE_TOLERANCE[item["slot"]]) | (from_skin < from_item))
    else:
        foreign = area & (from_skin < PRINT_FROM_SKIN)
    print(f"  {item['id']}: repainting {int(foreign.sum())} edge texels")
    good = ~foreign if own is None else own & ~foreign
    filled = texture.copy()
    for _ in range(40):
        if not foreign.any():
            break
        total = np.zeros_like(filled)
        count = np.zeros((h, w), np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                shifted_good = np.roll(np.roll(good, dy, 0), dx, 1)
                total += np.roll(np.roll(filled, dy, 0), dx, 1) * shifted_good[..., None]
                count += shifted_good
        grow = foreign & (count > 0)
        filled[grow] = total[grow] / count[grow, None]
        good |= grow
        foreign &= ~grow
    return Image.fromarray(filled.clip(0, 255).astype(np.uint8))


# How far items sit off the skin, as a share of height. Enough that the body's
# triangles between vertices can't poke through near an item's edge.
PUSH_MARGIN = {"hair": 0.006, "top": 0.005, "bottom": 0.005, "shoes": 0.005}


PUSH_SMOOTHING = 8


def neighbor_mean(values, adjacency):
    """Average of each vertex's neighbors (across seams) for signed values;
    rig.neighbor_share is for masks."""
    neighbors, starts, welded = adjacency
    ids = welded.max() + 1
    merged = np.zeros(ids)
    merged[welded] = values
    sources = np.repeat(np.arange(ids), np.diff(starts))
    total = np.bincount(sources, weights=merged[neighbors], minlength=ids)
    return (total / np.maximum(np.bincount(sources, minlength=ids), 1))[welded]


def push_outside(points, triangles, base, match, margin):
    """Moves item points out to `margin` in front of the nearest body point's tangent
    plane, so the body doesn't poke through as soon as it moves. The moves are smoothed
    over the item so neighbors that need different pushes don't fold it into dents;
    wherever the body still sticks out, body_triangles_under hides it."""
    normals = base.normals[match] / np.linalg.norm(base.normals[match], axis=1, keepdims=True)
    offset = points - base.points[match]
    depth = np.einsum("ij,ij->i", offset, normals)
    # Only points actually close to the body: clothes far from it (a wide hem) stay put.
    close = np.linalg.norm(offset, axis=1) < margin * 8
    move = normals * (np.maximum(margin - depth, 0) * close)[:, None]
    adjacency = rig.vertex_adjacency(triangles.ravel(), points)
    for _ in range(PUSH_SMOOTHING):
        move = 0.5 * move + 0.5 * np.stack([neighbor_mean(move[:, k], adjacency) for k in range(3)], axis=1)
    return points + move


def transfer_weights(points, triangles, base, joint_count, k=6, passes=10):
    """Bone weights for an item: a blend of the nearest base vertices' weights, smoothed
    over the item's own surface. Copying only the single nearest vertex lets neighbors on a
    sleeve pick the arm and the chest under the armpit, and the sleeve tears when the arm rises."""
    targets = base.points.astype(np.float32)
    squared = (targets ** 2).sum(axis=1)
    dense = np.zeros((len(points), joint_count), np.float32)
    base_dense = np.zeros((len(base.points), joint_count), np.float32)
    for column in range(4):
        np.add.at(base_dense, (np.arange(len(base.points)), base.joints[:, column]), base.weights[:, column])
    for start in range(0, len(points), 1500):
        q = points[start:start + 1500].astype(np.float32)
        d = squared[None, :] - 2 * q @ targets.T + (q ** 2).sum(axis=1)[:, None]
        index = np.argpartition(d, k, axis=1)[:, :k]
        distance = np.sqrt(np.maximum(np.take_along_axis(d, index, axis=1), 0))
        share = 1 / (distance + 1e-4) ** 2
        share /= share.sum(axis=1, keepdims=True)
        dense[start:start + 1500] = (base_dense[index] * share[..., None]).sum(axis=1)
    order = np.argsort(-dense, axis=1)[:, :4]
    top = np.take_along_axis(dense, order, axis=1)
    top /= np.maximum(top.sum(axis=1, keepdims=True), 1e-9)
    adjacency = rig.vertex_adjacency(triangles.ravel(), points)
    return rig.smooth_weights(order.astype(np.uint8), top.astype(np.float32), adjacency,
                              np.zeros(len(points), int), joint_count, iterations=passes)


def write_item(path, outfit, base, skeleton_names, mask, item, height, reference, skin):
    all_points, all_normals, all_uvs, triangles = clip_item(outfit, mask, item)
    used = np.unique(triangles)
    remap = np.full(len(all_points), -1)
    remap[used] = np.arange(len(used))
    points = all_points[used]
    normals = all_normals[used]
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)

    match = nearest(points, base.points)
    points = push_outside(points, remap[triangles], base, match, height * PUSH_MARGIN[item["slot"]])
    joints, weights = transfer_weights(points, remap[triangles], base, len(skeleton_names))
    if item["slot"] == "hair":
        joints[:] = [skeleton_names.index("Head"), 0, 0, 0]
        weights[:] = [1, 0, 0, 0]

    writer = rig.GlbWriter()
    pbr = outfit.material.get("pbrMetallicRoughness", {})
    images, textures = [], []
    for info, size in ((pbr.get("baseColorTexture"), 1024), (outfit.material.get("normalTexture"), 512)):
        if info:
            image = Image.open(io.BytesIO(outfit.texture(info))).convert("RGB")
            if info is pbr.get("baseColorTexture"):
                image = clean_edges(image, all_points, all_uvs, triangles, reference, skin, item)
            image.thumbnail((size, size))
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
    gltf = skeleton_document(base, writer, item["id"], attributes, indices, material, textures, images)
    writer.write(path, gltf)
    print(f"  wrote {os.path.basename(path)}: {len(used)} vertices, {len(triangles)} triangles")
    return points, normals


def skeleton_document(base, writer, name, attributes, indices, material, textures, images):
    """A glTF with the base's skeleton nodes and inverse binds, and one skinned mesh."""
    nodes = json.loads(json.dumps(base.gltf["nodes"]))
    skin = json.loads(json.dumps(base.gltf["skins"][0]))
    mesh_node = next(i for i, n in enumerate(nodes) if "mesh" in n)
    nodes[mesh_node] = {"name": name, "mesh": 0, "skin": 0}
    binds = rig.accessor(base.gltf, base.binary, skin["inverseBindMatrices"])
    skin["inverseBindMatrices"] = writer.accessor(binds.astype(np.float32).reshape(-1, 16), "MAT4", 5126)
    return {
        "asset": {"version": "2.0", "generator": "AvatarKit fit-outfits.py"},
        "scene": 0, "scenes": base.gltf["scenes"], "nodes": nodes,
        "meshes": [{"name": name, "primitives": [{"attributes": attributes, "indices": indices, "material": 0}]}],
        "skins": [skin], "materials": [material], "textures": textures, "images": images, "samplers": [{}],
    }


def write_base(path, base, keys, height):
    """The base split into patches by the set of items covering each triangle (`keys`).
    Each patch holds only its own vertices: SceneKit slows to a crawl on vertices no
    triangle uses, and there are dozens of patches."""
    gltf = json.loads(json.dumps(base.gltf))
    triangles = base.indices
    groups = {}
    for t, key in enumerate(keys):
        groups.setdefault(key, []).append(t)

    writer = rig.GlbWriter()
    primitive = gltf["meshes"][0]["primitives"][0]
    attributes = {semantic: base.points.astype(np.float32) if semantic == "POSITION"
                  else rig.accessor(base.gltf, base.binary, index)
                  for semantic, index in primitive["attributes"].items()}
    images = []
    for image in gltf.get("images", []):
        images.append({"bufferView": writer.view(rig.image_bytes(base.gltf, base.binary, gltf["images"].index(image))),
                       "mimeType": image.get("mimeType", "image/jpeg")})
    binds = rig.accessor(base.gltf, base.binary, gltf["skins"][0]["inverseBindMatrices"])
    gltf["skins"][0]["inverseBindMatrices"] = writer.accessor(binds.astype(np.float32).reshape(-1, 16), "MAT4", 5126)

    mesh_node_index = next(i for i, n in enumerate(gltf["nodes"]) if "mesh" in n)
    mesh_node = gltf["nodes"][mesh_node_index]
    armature = next(i for i, n in enumerate(gltf["nodes"]) if mesh_node_index in n.get("children", []))
    name = mesh_node["name"]
    meshes, new_nodes = [], []
    for key, rows in sorted(groups.items()):
        used, part = np.unique(triangles[rows].ravel(), return_inverse=True)
        accessors = {}
        for semantic, data in attributes.items():
            data = data[used]
            kind = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}[data.shape[1] if data.ndim > 1 else 1]
            component = {np.dtype(np.float32): 5126, np.dtype(np.uint8): 5121, np.dtype(np.uint16): 5123}[data.dtype]
            accessors[semantic] = writer.accessor(data, kind, component, 34962, bounds=semantic == "POSITION")
        index_type, component = (np.uint16, 5123) if len(used) < 65536 else (np.uint32, 5125)
        prim = {"attributes": accessors, "material": primitive["material"],
                "indices": writer.accessor(part.astype(index_type), "SCALAR", component, 34963)}
        meshes.append({"name": f"{name}__covers__{key}" if key else name, "primitives": [prim]})
        new_nodes.append({"name": meshes[-1]["name"], "mesh": len(meshes) - 1, "skin": 0})
    gltf["meshes"] = meshes
    first = len(gltf["nodes"])
    gltf["nodes"].extend(new_nodes)
    gltf["nodes"][armature]["children"] = [c for c in gltf["nodes"][armature]["children"] if c != mesh_node_index] \
        + list(range(first, first + len(new_nodes)))
    gltf["nodes"][mesh_node_index] = {"name": f"{name}_unused"}
    gltf["images"] = images
    writer.write(path, gltf)
    print(f"wrote {os.path.basename(path)}: {len(groups)} patches")


def body_triangles_under(base, surface, normals, height, radius=0.004, depth=0.03):
    """Body triangles to hide under an item. Each triangle is sampled at its corners,
    edge middles and center, and every sample is compared with the nearest point of
    the item: straight under it within `depth` counts as covered, in front of it as
    poking out. Samples beside the item's edge (the neck above a collar) are neither.
    A triangle is hidden when all its samples are covered or any of them pokes out."""
    corners = base.points[base.indices]
    samples = np.concatenate([corners, (corners + np.roll(corners, 1, axis=1)) / 2,
                              corners.mean(axis=1, keepdims=True)], axis=1)
    flat = samples.reshape(-1, 3)
    match = nearest(flat, surface)
    offset = flat - surface[match]
    side = np.einsum("ij,ij->i", offset, normals[match])
    across = np.linalg.norm(offset - side[:, None] * normals[match], axis=1)
    straight = (across < height * radius) & (np.abs(side) < height * depth)
    covered = (straight & (side <= 0)).reshape(len(base.indices), -1)
    poking = (straight & (side > 0)).reshape(len(base.indices), -1)
    return (covered | poking).all(axis=1) | poking.any(axis=1)


def triangle_adjacency(base):
    """Pairs of triangles sharing an edge, joined across UV seams."""
    _, _, welded = rig.vertex_adjacency(base.indices.ravel(), base.points)
    corners = welded[base.indices]
    edges = np.sort(np.stack([corners[:, [0, 1]], corners[:, [1, 2]], corners[:, [2, 0]]], axis=1).reshape(-1, 2), axis=1)
    code = edges[:, 0].astype(np.int64) * (welded.max() + 1) + edges[:, 1]
    order = np.argsort(code, kind="stable")
    same = code[order][1:] == code[order][:-1]
    return order[:-1][same] // 3, order[1:][same] // 3


def triangle_parts(count, a, b):
    """Connected parts over the given triangle pairs, as the smallest triangle index in each."""
    part = np.arange(count)
    while True:
        before = part.copy()
        np.minimum.at(part, a, part[b])
        np.minimum.at(part, b, part[a])
        for _ in range(4):
            part = part[part]
        if np.array_equal(part, before):
            return part


def fill_cover_holes(covered, adjacency, largest=0.05):
    """Covers the uncovered islands inside an item's area (smaller than `largest` of it):
    loose clothes sit too far off the body for some triangles under them to count as
    covered, and the speckles would split the body into many tiny patches."""
    a, b = adjacency
    both = ~covered[a] & ~covered[b]
    part = triangle_parts(len(covered), a[both], b[both])
    sizes = np.bincount(part[~covered], minlength=len(covered))
    return covered | (~covered & (sizes[part] < covered.sum() * largest))


SMALLEST_PATCH = 16


def merge_slivers(keys, adjacency, smallest=SMALLEST_PATCH):
    """Gives each connected patch of fewer than `smallest` triangles the key most of its
    neighbors have. Where two items' edges nearly meet, their covers leave slivers of a
    few triangles, each of which would be a patch of its own."""
    a, b = adjacency
    names, label = np.unique(keys, return_inverse=True)
    for _ in range(20):
        joined = label[a] == label[b]
        part = triangle_parts(len(label), a[joined], b[joined])
        small = np.bincount(part, minlength=len(label))[part] < smallest
        out_a, out_b = ~joined & small[a], ~joined & small[b]
        parts = np.concatenate([part[a][out_a], part[b][out_b]]).astype(np.int64)
        if not len(parts):
            break
        votes = np.concatenate([label[b][out_a], label[a][out_b]])
        pair, count = np.unique(parts * len(names) + votes, return_counts=True)
        order = np.lexsort((-count, pair // len(names)))
        voter = (pair // len(names))[order]
        first = np.r_[True, voter[1:] != voter[:-1]]
        choice = np.full(len(label), -1)
        choice[voter[first]] = (pair % len(names))[order][first]
        movers = small & (choice[part] >= 0)
        label[movers] = choice[part][movers]
    return list(names[label])


# MARK: - Main

def main():
    base_path, outfits_path, out_dir = sys.argv[1:4]
    os.makedirs(out_dir, exist_ok=True)
    base = Mesh(base_path)
    skeleton = rig.Skeleton(base.gltf, base.binary)
    height = float(np.ptp(base.points[:, 1]))
    samples = rig.surface_samples(base.points, base.indices.ravel(), 300_000)
    marks = rig.measure(samples, height)

    face_rows = np.abs(base.points[:, 1] - marks["nose"]) < height * 0.02
    face = face_rows & (np.abs(base.points[:, 0]) < height * 0.03)
    face &= base.points[:, 2] > np.percentile(base.points[face_rows, 2], 90)
    skin_color = np.median(base.colors[face], axis=0)

    covers = [set() for _ in range(len(base.indices))]
    adjacency = triangle_adjacency(base)
    summary = []
    for outfit_spec in json.load(open(outfits_path)):
        print(outfit_spec["model"])
        outfit = Mesh(outfit_spec["model"])
        outfit.forward = outfit_spec.get("forward", "+z")
        align(outfit, base, skin_color, height)
        labels = split_items(outfit, base, marks, height, outfit_spec["items"])
        for column, item in enumerate(outfit_spec["items"]):
            mask = labels == column
            item_points, item_normals = write_item(os.path.join(out_dir, f"{item['id']}.glb"), outfit, base,
                                                   skeleton.names, mask, item, height, outfit.references[column],
                                                   lab(skin_color[None])[0])
            if item["slot"] == "hair":
                continue  # the scalp stays; hair sits just outside it
            # Hide body triangles wholly under the item, and any that stick out through it.
            # Triangles at the item's edge stay: hiding them opens triangle-shaped holes
            # beside the edge, and push_outside keeps the item in front of them.
            covered = fill_cover_holes(body_triangles_under(base, item_points, item_normals, height), adjacency)
            for t in np.where(covered)[0]:
                covers[t].add(item["id"])
            summary.append({"id": item["id"], "slot": item["slot"], "file": f"{item['id']}.glb"})

    keys = merge_slivers(["+".join(sorted(ids)) for ids in covers], adjacency)
    write_base(os.path.join(out_dir, "base.glb"), base, keys, height)
    json.dump(summary, open(os.path.join(out_dir, "items.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
