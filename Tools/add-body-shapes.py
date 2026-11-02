#!/usr/bin/env python3
"""Adds body types (morph targets) to a fitted base body and every item cut for it.

    python3 add-body-shapes.py <rigged-base.glb> <fitted-dir> <name>=<shape.glb> [...] [--forward +x]

Each shape is the base character generated again with another build ("very skinny",
"heavy"; Tools/tripo/generate.mjs), unrigged and with its own mesh. It's lined up with
the base by the head, which doesn't change, and the base's surface is pulled onto it
step by step (non-rigid ICP, kept smooth), so the shape becomes a morph target of the
base mesh itself. Head, hands and feet stay put: faces, hair and shoes keep fitting.

Every GLB in fitted-dir (base.glb's patches and each item from fit-outfits.py) gets the
same targets in the same order; an item vertex moves with the base vertices nearest to
it. Items bound only to the head (hair) get none. The app shows a body type by setting
its target's weight to 1.
"""

import importlib.util
import json
import os
import sys

import numpy as np

np.seterr(all="ignore")
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("rig", os.path.join(here, "rig-generated-body.py"))
rig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rig)

TURN = {"+z": np.eye(3), "-z": np.diag([-1, 1, -1]),
        "+x": np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]]), "-x": np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]])}

# Bones whose skin doesn't change between body types.
FIXED_BONES = ("Head", "hand_", "index_", "middle_", "ring_", "pinky_", "thumb_", "foot_", "ball_")
# Limbs change thickness, not place (remove_limb_shifts).
LIMB_BONES = ("thigh_", "calf_", "upperarm_", "lowerarm_")

SAMPLES = 120_000
ITERATIONS = 40
# How far (share of height) a base point may be pulled; farther matches are wrong parts.
MAX_PULL = 0.15


# MARK: - Reading

def read_mesh(path):
    """Positions (node transform applied), normals and triangles of every primitive, plus skin data of the first."""
    gltf, binary = rig.read_glb(path)
    parts = []
    for node in gltf.get("nodes", []):
        if "mesh" not in node:
            continue
        matrix = node_matrix(node)
        for primitive in gltf["meshes"][node["mesh"]]["primitives"]:
            attributes = primitive["attributes"]
            points = rig.accessor(gltf, binary, attributes["POSITION"]).astype(np.float64)
            parts.append({
                "points": points @ matrix[:3, :3].T + matrix[:3, 3],
                "indices": rig.accessor(gltf, binary, primitive["indices"]).astype(np.int64).reshape(-1, 3),
                "joints": rig.accessor(gltf, binary, attributes["JOINTS_0"]) if "JOINTS_0" in attributes else None,
                "weights": rig.accessor(gltf, binary, attributes["WEIGHTS_0"]) if "WEIGHTS_0" in attributes else None,
            })
    return gltf, binary, parts


def node_matrix(node):
    if "matrix" in node:
        return np.array(node["matrix"], np.float64).reshape(4, 4).T
    matrix = np.eye(4)
    matrix[:3, :3] = rig.quat_to_matrix(node.get("rotation", [0, 0, 0, 1])) * np.array(node.get("scale", [1, 1, 1]))
    matrix[:3, 3] = node.get("translation", [0, 0, 0])
    return matrix


def merged(parts):
    points, indices, offset = [], [], 0
    for part in parts:
        points.append(part["points"])
        indices.append(part["indices"] + offset)
        offset += len(part["points"])
    return np.concatenate(points), np.concatenate(indices)


# MARK: - Geometry helpers

def vertex_normals(points, indices):
    triangles = points[indices]
    face = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals = np.zeros_like(points)
    for k in range(3):
        np.add.at(normals, indices[:, k], face)
    return normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)


def samples_with_normals(points, indices, count):
    """Points spread over the surface, each with its triangle's normal."""
    triangles = points[indices]
    face = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    areas = np.linalg.norm(face, axis=1)
    rng = np.random.default_rng(3)
    chosen = rng.choice(len(triangles), count, p=areas / areas.sum())
    u, v = rng.random(count), rng.random(count)
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    t = triangles[chosen]
    samples = t[:, 0] + u[:, None] * (t[:, 1] - t[:, 0]) + v[:, None] * (t[:, 2] - t[:, 0])
    return samples, face[chosen] / np.maximum(areas[chosen], 1e-12)[:, None]


def nearest(queries, targets, k=1, chunk=512):
    """Indices (and distances) of the k nearest targets for each query, brute force in chunks."""
    targets32 = targets.astype(np.float32)
    squared = (targets32 ** 2).sum(axis=1)
    index = np.empty((len(queries), k), np.int64)
    distance = np.empty((len(queries), k), np.float32)
    for start in range(0, len(queries), chunk):
        q = queries[start:start + chunk].astype(np.float32)
        d = squared[None, :] - 2 * q @ targets32.T + (q ** 2).sum(axis=1)[:, None]
        if k == 1:
            best = np.argmin(d, axis=1)[:, None]
        else:
            best = np.argpartition(d, k, axis=1)[:, :k]
        index[start:start + chunk] = best
        distance[start:start + chunk] = np.sqrt(np.maximum(np.take_along_axis(d, best, axis=1), 0))
    return index, distance


def smooth(values, adjacency, passes, weights=None):
    """Averages each vertex with its neighbors (across UV seams), `passes` times."""
    neighbors, starts, welded = adjacency
    ids = welded.max() + 1
    sources = np.repeat(np.arange(ids), np.diff(starts))
    weights = np.ones(len(values)) if weights is None else weights
    total = np.zeros((ids, values.shape[1]))
    mass = np.zeros(ids)
    np.add.at(total, welded, values * weights[:, None])
    np.add.at(mass, welded, weights)
    for _ in range(passes):
        spread = total.copy()
        spread_mass = mass.copy()
        np.add.at(spread, sources, total[neighbors])
        np.add.at(spread_mass, sources, mass[neighbors])
        degree = np.bincount(sources, minlength=ids) + 1
        total, mass = spread / degree[:, None], spread_mass / degree
    return (total / np.maximum(mass, 1e-9)[:, None])[welded]


# MARK: - Fitting a shape

def fixed_share(base, names):
    """0...1 per base vertex: how much it belongs to bones that keep their shape."""
    fixed = np.array([name.startswith(FIXED_BONES) for name in names] + [False])
    joints = np.where(base["joints"] < len(names), base["joints"], len(names)).astype(np.int64)
    return np.clip((base["weights"] * fixed[joints]).sum(axis=1), 0, 1)


def place(shape_points, forward, base_points, height, neck):
    """Turns, scales and moves the shape onto the base, then refines on the head (rigid ICP)."""
    points = shape_points @ TURN[forward].T
    points *= height / np.ptp(points[:, 1])
    points[:, 1] += base_points[:, 1].min() - points[:, 1].min()
    head_base = base_points[base_points[:, 1] > neck]
    head = points[:, 1] > neck
    points[:, [0, 2]] += np.median(head_base[:, [0, 2]], axis=0) - np.median(points[head][:, [0, 2]], axis=0)
    for _ in range(20):
        candidates = np.where(points[:, 1] > neck - height * 0.03)[0]
        match, distance = nearest(head_base, points[candidates])
        keep = distance[:, 0] < np.percentile(distance[:, 0], 85)
        source, target = points[candidates[match[keep, 0]]], head_base[keep]
        mu_s, mu_t = source.mean(axis=0), target.mean(axis=0)
        u, d, vt = np.linalg.svd((target - mu_t).T @ (source - mu_s) / len(source))
        rotation = u @ np.diag([1, 1, np.sign(np.linalg.det(u @ vt))]) @ vt
        scale = d.sum() / ((source - mu_s) ** 2).sum(axis=1).mean()
        points = scale * (points - mu_s) @ rotation.T + mu_t
    print(f"  head lined up: median gap {np.median(distance) / height:.4f} of height")
    return points


def fit_shape(base_points, indices, adjacency, mobility, shape_points, shape_indices, height):
    """Pulls the base surface onto the shape's: offsets along the matched surface's normal, smoothed."""
    samples, sample_normals = samples_with_normals(shape_points, shape_indices, SAMPLES)
    # Matching in 6D (position + scaled normal) keeps a front-facing point off a back-facing surface.
    lam = height * 0.05
    targets = np.hstack([samples, sample_normals * lam])
    moved = base_points.copy()
    for step in range(ITERATIONS):
        normals = vertex_normals(moved, indices)
        match, _ = nearest(np.hstack([moved, normals * lam]), targets)
        match = match[:, 0]
        gap = samples[match] - moved
        along = (gap * sample_normals[match]).sum(axis=1)
        pull = along[:, None] * sample_normals[match]
        valid = (np.linalg.norm(gap, axis=1) < height * MAX_PULL) & ((normals * sample_normals[match]).sum(axis=1) > 0.3)
        passes = int(np.interp(step, [0, ITERATIONS - 1], [24, 4]))
        pull = smooth(np.where(valid[:, None], pull, 0), adjacency, passes, weights=valid.astype(float) + 1e-3)
        moved += 0.5 * mobility[:, None] * pull
    gap = np.linalg.norm(samples[nearest(moved, samples)[0][:, 0]] - moved, axis=1)
    loose = mobility > 0.5
    print(f"  fitted: median gap {np.median(gap[loose]) / height:.4f}, 95% {np.percentile(gap[loose], 95) / height:.4f} of height")
    delta = smooth(moved - base_points, adjacency, 2) * mobility[:, None]
    return delta


# A hand or foot this far (share of height) from the shape's surface means the generated
# build stands differently; the fit would bend the limb to reach it.
POSE_TOLERANCE = 0.04


def warn_if_posed_differently(name, base, names, shape_points, shape_indices, height):
    samples, _ = samples_with_normals(shape_points, shape_indices, 60_000)
    _, distance = nearest(base["points"], samples)
    for end in ("hand_l", "hand_r", "foot_l", "foot_r"):
        on = base["joints"][:, 0] == names.index(end)
        gap = float(np.median(distance[on, 0])) / height
        if gap > POSE_TOLERANCE:
            print(f"  WARNING {name}: {end} is {gap:.3f} of height from the shape; it stands differently "
                  f"from the base. Regenerate it (generate.mjs image ... --force).")


def remove_limb_shifts(delta, base, names):
    """Keeps only how thick each limb gets. Generated builds never repeat the base's exact
    stance, so a leg or arm also comes out shifted; the skeleton doesn't move with it, so
    a shifted limb would bend away from its bones and kink at the fixed hands and feet.
    Each limb bone's average move (by skin weight) is taken out; the torso keeps its own."""
    weights = np.zeros((len(delta), len(names)))
    for k in range(4):
        np.add.at(weights, (np.arange(len(delta)), base["joints"][:, k]), base["weights"][:, k])
    shift = np.zeros_like(delta)
    for index, name in enumerate(names):
        share = weights[:, index]
        if name.startswith(LIMB_BONES) and share.sum() > 1e-6:
            shift += share[:, None] * ((share[:, None] * delta).sum(axis=0) / share.sum())
    return delta - shift


# MARK: - Writing targets

def add_targets(path, names, position_delta, normal_delta, base_points, skip_head_only=None):
    """Adds the targets to every primitive of the GLB; deltas follow the nearest base vertices."""
    gltf, binary, _ = read_mesh(path)
    writer = rig.GlbWriter()
    writer.binary = bytearray(binary)
    writer.views = gltf.get("bufferViews", [])
    writer.accessors = gltf.get("accessors", [])
    # base.glb's patches share one vertex buffer; they share its targets too.
    shared = {}
    for mesh in gltf["meshes"]:
        for primitive in mesh["primitives"]:
            attributes = primitive["attributes"]
            if attributes["POSITION"] in shared:
                primitive["targets"] = shared[attributes["POSITION"]]
                continue
            points = rig.accessor(gltf, binary, attributes["POSITION"]).astype(np.float64)
            if skip_head_only is not None and "JOINTS_0" in attributes:
                joints = rig.accessor(gltf, binary, attributes["JOINTS_0"])
                weights = rig.accessor(gltf, binary, attributes["WEIGHTS_0"])
                if np.all((joints[:, 0] == skip_head_only) & (weights[:, 0] > 0.999)):
                    return False
            match, distance = nearest(points, base_points, k=4)
            weight = 1 / np.maximum(distance, 1e-6) ** 2
            weight /= weight.sum(axis=1, keepdims=True)
            targets = []
            for t in range(len(names)):
                moved = (position_delta[t][match] * weight[..., None]).sum(axis=1).astype(np.float32)
                turned = (normal_delta[t][match] * weight[..., None]).sum(axis=1).astype(np.float32)
                target = {"POSITION": writer.accessor(moved, "VEC3", 5126, 34962, bounds=True)}
                if "NORMAL" in attributes:
                    target["NORMAL"] = writer.accessor(turned, "VEC3", 5126, 34962)
                targets.append(target)
            primitive["targets"] = targets
            shared[attributes["POSITION"]] = targets
        mesh["weights"] = [0.0] * len(names)
        mesh.setdefault("extras", {})["targetNames"] = list(names)
    writer.write(path, gltf)
    return True


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    forward = sys.argv[sys.argv.index("--forward") + 1] if "--forward" in sys.argv else "+x"
    args = [a for a in args if a != forward]
    base_path, fitted = args[0], args[1]
    shapes = [arg.split("=", 1) for arg in args[2:]]

    gltf, binary, parts = read_mesh(base_path)
    base = parts[0]
    names = rig.Skeleton(gltf, binary).names
    height = float(np.ptp(base["points"][:, 1]))
    marks = rig.measure(rig.surface_samples(base["points"], base["indices"].ravel(), 300_000), height)
    adjacency = rig.vertex_adjacency(base["indices"].ravel(), base["points"])
    mobility = 1 - smooth(fixed_share(base, names)[:, None], adjacency, 3)[:, 0] ** 0.5

    position_deltas, normal_deltas = [], []
    for name, path in shapes:
        print(f"{name}: {path}")
        _, _, shape_parts = read_mesh(path)
        shape_points, shape_indices = merged(shape_parts)
        shape_points = place(shape_points, forward, base["points"], height, marks["neck"] + height * 0.02)
        warn_if_posed_differently(name, base, names, shape_points, shape_indices, height)
        delta = fit_shape(base["points"], base["indices"], adjacency, mobility, shape_points, shape_indices, height)
        delta = remove_limb_shifts(delta, base, names) * mobility[:, None]
        normal_delta = vertex_normals(base["points"] + delta, base["indices"]) - vertex_normals(base["points"], base["indices"])
        print(f"  largest move {np.linalg.norm(delta, axis=1).max() / height:.3f} of height")
        position_deltas.append(delta)
        normal_deltas.append(normal_delta)

    target_names = [name for name, _ in shapes]
    head = names.index("Head")
    for file in sorted(os.listdir(fitted)):
        if not file.endswith(".glb"):
            continue
        added = add_targets(os.path.join(fitted, file), target_names, position_deltas, normal_deltas, base["points"],
                            skip_head_only=None if file == "base.glb" else head)
        print(f"  {file}: {'shapes added' if added else 'head only, no shapes'}")


if __name__ == "__main__":
    main()
