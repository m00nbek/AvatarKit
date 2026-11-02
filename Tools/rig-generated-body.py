#!/usr/bin/env python3
"""Rigs a generated, unrigged character mesh (Tripo, Meshy, ...) to the
avatar skeleton, so it plays the same animations as the other bodies.

    python3 rig-generated-body.py <mesh.glb> <template-body.glb> <out.glb> [--forward +x] [--name Name]

The mesh must stand upright with arms hanging down. Joints are placed from
the mesh's shape (crotch, armpits, hands, neck), the template skeleton keeps
its bone orientations so animation clips still apply, and skin weights come
from distance to bone segments, limited to the limb each vertex belongs to.
Rough, but enough to judge a model in the app before rigging it properly.
"""

import argparse
import io
import json
import struct

import numpy as np
from PIL import Image

# NumPy 2.0 with Apple's Accelerate reports spurious floating-point warnings from matmul.
np.seterr(all="ignore")

# MARK: - GLB reading and writing

COMPONENT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read_glb(path):
    data = open(path, "rb").read()
    json_length = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20:20 + json_length])
    binary = data[20 + json_length + 8:]
    return gltf, binary


def accessor(gltf, binary, index):
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    dtype = COMPONENT[acc["componentType"]]
    width = WIDTH[acc["type"]]
    offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride", 0)
    item = np.dtype(dtype).itemsize * width
    if stride and stride != item:
        raw = np.frombuffer(binary, np.uint8, acc["count"] * stride, offset).reshape(-1, stride)[:, :item]
        values = np.frombuffer(raw.tobytes(), dtype)
    else:
        values = np.frombuffer(binary, dtype, acc["count"] * width, offset)
    return values.reshape(-1, width) if width > 1 else values


def image_bytes(gltf, binary, index):
    view = gltf["bufferViews"][gltf["images"][index]["bufferView"]]
    start = view.get("byteOffset", 0)
    return binary[start:start + view["byteLength"]]


class GlbWriter:
    def __init__(self):
        self.binary = bytearray()
        self.views, self.accessors = [], []

    def view(self, payload, target=None):
        while len(self.binary) % 4:
            self.binary.append(0)
        entry = {"buffer": 0, "byteOffset": len(self.binary), "byteLength": len(payload)}
        if target:
            entry["target"] = target
        self.binary.extend(payload)
        self.views.append(entry)
        return len(self.views) - 1

    def accessor(self, array, kind, component, target=None, bounds=False):
        array = np.ascontiguousarray(array)
        entry = {"bufferView": self.view(array.tobytes(), target), "componentType": component,
                 "count": len(array), "type": kind}
        if bounds:
            entry["min"] = array.min(axis=0).tolist()
            entry["max"] = array.max(axis=0).tolist()
        self.accessors.append(entry)
        return len(self.accessors) - 1

    def write(self, path, gltf):
        gltf["buffers"] = [{"byteLength": len(self.binary)}]
        gltf["bufferViews"] = self.views
        gltf["accessors"] = self.accessors
        text = json.dumps(gltf, separators=(",", ":")).encode()
        text += b" " * (-len(text) % 4)
        while len(self.binary) % 4:
            self.binary.append(0)
        total = 12 + 8 + len(text) + 8 + len(self.binary)
        with open(path, "wb") as out:
            out.write(struct.pack("<III", 0x46546C67, 2, total))
            out.write(struct.pack("<II", len(text), 0x4E4F534A) + text)
            out.write(struct.pack("<II", len(self.binary), 0x004E4942) + bytes(self.binary))


# MARK: - Math

def quat_to_matrix(q):
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def matrix_to_quat(m):
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = 0.5 / np.sqrt(trace + 1)
        q = [(m[2, 1] - m[1, 2]) * s, (m[0, 2] - m[2, 0]) * s, (m[1, 0] - m[0, 1]) * s, 0.25 / s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = 2 * np.sqrt(1 + m[0, 0] - m[1, 1] - m[2, 2])
        q = [0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
    elif m[1, 1] > m[2, 2]:
        s = 2 * np.sqrt(1 + m[1, 1] - m[0, 0] - m[2, 2])
        q = [(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
    else:
        s = 2 * np.sqrt(1 + m[2, 2] - m[0, 0] - m[1, 1])
        q = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


def rotation_between(a, b):
    """Shortest rotation matrix taking direction a to direction b."""
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    v, c = np.cross(a, b), np.dot(a, b)
    if c < -0.9999:
        axis = np.cross(a, [1, 0, 0]) if abs(a[0]) < 0.9 else np.cross(a, [0, 1, 0])
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def surface_samples(points, indices, count, seed=1):
    """Points spread evenly over the surface; vertices alone are too sparse to slice."""
    triangles = points[indices.reshape(-1, 3)]
    areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1)
    rng = np.random.default_rng(seed)
    chosen = rng.choice(len(triangles), count, p=areas / areas.sum())
    u, v = rng.random(count), rng.random(count)
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    t = triangles[chosen]
    return t[:, 0] + u[:, None] * (t[:, 1] - t[:, 0]) + v[:, None] * (t[:, 2] - t[:, 0])


def vertex_adjacency(indices, points):
    """Edges between vertices, joined across UV seams: seams duplicate vertices at
    the same position, which would otherwise split the surface into islands."""
    _, welded = np.unique(np.round(points / (np.ptp(points[:, 1]) * 1e-5)).astype(np.int64), axis=0, return_inverse=True)
    welded = welded.ravel()
    count = len(points)
    triangles = welded[indices.reshape(-1, 3)]
    edges = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    edges = np.concatenate([edges, edges[:, ::-1]])
    order = np.argsort(edges[:, 0])
    edges = edges[order]
    starts = np.searchsorted(edges[:, 0], np.arange(welded.max() + 2))
    return edges[:, 1], starts, welded


def connected_from(seeds, allowed, adjacency):
    """Vertices in `allowed` reachable from `seeds` along mesh edges."""
    neighbors, starts, welded = adjacency
    allowed_ids = np.zeros(welded.max() + 1, bool)
    allowed_ids[welded[allowed]] = True
    reached = np.zeros_like(allowed_ids)
    frontier = np.unique(welded[seeds & allowed])
    reached[frontier] = True
    while len(frontier):
        nxt = np.unique(np.concatenate([neighbors[starts[v]:starts[v + 1]] for v in frontier]))
        nxt = nxt[allowed_ids[nxt] & ~reached[nxt]]
        reached[nxt] = True
        frontier = nxt
    return reached[welded] & allowed


def smooth_weights(joints, weights, adjacency, group_of, joint_count, blend=(), iterations=8):
    """Averages each vertex's weights with its neighbors' (same limb group, or groups
    listed in `blend`), so neighboring vertices don't follow different bones and tear
    the surface apart."""
    neighbors, starts, welded = adjacency
    ids = welded.max() + 1
    dense = np.zeros((ids, joint_count), np.float32)
    for k in range(4):
        np.add.at(dense, (welded, joints[:, k]), weights[:, k])
    counts = np.bincount(welded, minlength=ids).astype(np.float32)
    dense /= np.maximum(counts, 1)[:, None]
    group = np.zeros(ids, int)
    group[welded] = group_of

    sources = np.repeat(np.arange(ids), np.diff(starts))
    same = group[sources] == group[neighbors]
    for a, b in blend:
        same |= ((group[sources] == a) & (group[neighbors] == b)) | ((group[sources] == b) & (group[neighbors] == a))
    sources, targets = sources[same], neighbors[same]
    degree = np.bincount(sources, minlength=ids).astype(np.float32)
    for _ in range(iterations):
        summed = np.zeros_like(dense)
        np.add.at(summed, sources, dense[targets])
        has = degree > 0
        dense[has] = 0.5 * dense[has] + 0.5 * summed[has] / degree[has, None]

    per_vertex = dense[welded]
    order = np.argsort(-per_vertex, axis=1)[:, :4]
    top = np.take_along_axis(per_vertex, order, axis=1)
    top /= np.maximum(top.sum(axis=1, keepdims=True), 1e-9)
    return order.astype(np.uint8), top.astype(np.float32)


def relax_weights(joints, weights, adjacency, region, joint_count, iterations=120):
    """Inside `region` (a vertex mask), each vertex's weights become the mean of its
    neighbors', many times over, while vertices outside keep theirs: a smooth blend
    across the region from whatever surrounds it. Around a shoulder it joins the arm's
    weights to the chest's and back's, so raising the arm stretches the skin instead of
    leaving the shoulder behind."""
    neighbors, starts, welded = adjacency
    ids = welded.max() + 1
    dense = np.zeros((ids, joint_count), np.float32)
    for k in range(4):
        np.add.at(dense, (welded, joints[:, k]), weights[:, k])
    dense /= np.maximum(np.bincount(welded, minlength=ids), 1)[:, None]
    inside = np.zeros(ids, bool)
    inside[welded[region]] = True
    sources = np.repeat(np.arange(ids), np.diff(starts))
    keep = inside[sources]
    sources, targets = sources[keep], neighbors[keep]
    rows = np.unique(sources)
    degree = np.bincount(sources, minlength=ids).astype(np.float32)
    for _ in range(iterations):
        summed = np.zeros((ids, joint_count), np.float32)
        np.add.at(summed, sources, dense[targets])
        dense[rows] = summed[rows] / degree[rows, None]
    per_vertex = dense[welded]
    order = np.argsort(-per_vertex, axis=1)[:, :4]
    top = np.take_along_axis(per_vertex, order, axis=1)
    top /= np.maximum(top.sum(axis=1, keepdims=True), 1e-9)
    return order.astype(np.uint8), top.astype(np.float32)


def neighbor_share(mask, adjacency):
    """For each vertex, the share of its neighbors (across seams) inside `mask`."""
    neighbors, starts, welded = adjacency
    ids = welded.max() + 1
    inside = np.zeros(ids, np.float32)
    np.maximum.at(inside, welded, mask.astype(np.float32))
    sources = np.repeat(np.arange(ids), np.diff(starts))
    hits = np.bincount(sources, weights=inside[neighbors], minlength=ids)
    degree = np.maximum(np.bincount(sources, minlength=ids), 1)
    return (hits / degree)[welded]


def segment_distance(points, a, b):
    ab = b - a
    t = np.clip(((points - a) @ ab) / max(ab @ ab, 1e-9), 0, 1)
    return np.linalg.norm(points - (a + t[:, None] * ab), axis=1)


# MARK: - Template skeleton

class Skeleton:
    def __init__(self, gltf, binary):
        nodes = gltf["nodes"]
        skin = gltf["skins"][0]
        self.joint_indices = skin["joints"]
        self.names = [nodes[j]["name"] for j in self.joint_indices]
        self.parent = {}
        for i, node in enumerate(nodes):
            for child in node.get("children", []):
                self.parent[child] = i
        self.nodes = nodes
        self.world_rotation, self.world_position = {}, {}
        for j in self.joint_indices:
            self._world(j)
        self.index = {name: j for name, j in zip(self.names, self.joint_indices)}

    def _world(self, i):
        if i in self.world_rotation:
            return self.world_rotation[i], self.world_position[i]
        node = self.nodes[i]
        r = quat_to_matrix(node.get("rotation", [0, 0, 0, 1]))
        t = np.array(node.get("translation", [0, 0, 0]), float)
        if i in self.parent:
            pr, pt = self._world(self.parent[i])
            r, t = pr @ r, pt + pr @ t
        self.world_rotation[i], self.world_position[i] = r, t
        return r, t

    def position(self, name):
        return self.world_position[self.index[name]]

    def rotation(self, name):
        return self.world_rotation[self.index[name]]

    def parent_name(self, name):
        parent = self.parent.get(self.index[name])
        return self.nodes[parent]["name"] if parent in self.joint_indices else None


# MARK: - Measuring the mesh

def intervals(values, lo, hi, bins):
    """Occupied runs of a 1-D histogram, as (start, end) value pairs."""
    hist, edges = np.histogram(values, bins=bins, range=(lo, hi))
    runs, start = [], None
    for i, count in enumerate(hist):
        if count and start is None:
            start = i
        if not count and start is not None:
            runs.append((edges[start], edges[i]))
            start = None
    if start is not None:
        runs.append((edges[start], edges[-1]))
    return runs


def measure(points, height):
    x, y, z = points[:, 0], points[:, 1], points[:, 2]
    band = height * 0.012
    center_z = np.median(z)
    marks = {}

    # Crotch: below it, the middle of the body sits further back than the legs,
    # even when wide trousers touch. Scan down from the hips for that dip.
    def front_at(cx, row):
        near = (np.abs(x - cx) < height * 0.012) & (np.abs(y - row) < band)
        return z[near].max(initial=-np.inf)

    leg_x = height * 0.06
    marks["crotch"] = height * 0.45
    for row in np.arange(height * 0.65, height * 0.15, -band / 2):
        legs_front = min(front_at(-leg_x, row), front_at(leg_x, row))
        if legs_front - front_at(0, row) > height * 0.012:
            marks["crotch"] = row
            break

    # Face profile: the nose sticks out most, the neck is the deepest dip below it.
    middle = np.abs(x) < height * 0.05
    rows = np.arange(height * 0.55, height * 0.98, band)
    front = np.array([z[middle & (np.abs(y - r) < band)].max(initial=-np.inf) for r in rows])
    head_rows = rows > height * 0.8
    nose_row = np.argmax(np.where(head_rows, front, -np.inf))
    marks["nose"] = rows[nose_row]
    search = (rows > height * 0.62) & (rows < marks["nose"] - height * 0.05)
    marks["neck"] = rows[np.argmin(np.where(search, front, np.inf))]
    marks["front_z"] = lambda row: front[np.argmin(np.abs(rows - row))]

    # Arms: below the armpits the front half of a slice splits into arm, torso, arm.
    front_half = z > center_z - height * 0.02
    arm_rows = []
    for row in np.arange(marks["crotch"] - height * 0.1, marks["neck"], band):
        runs = intervals(x[front_half & (np.abs(y - row) < band)], -height * 0.4, height * 0.4, 160)
        if len(runs) >= 3:
            arm_rows.append((row, runs[0], runs[-1]))
    armpit = max(r for r, _, _ in arm_rows if r < marks["neck"] - height * 0.04)
    # Follow the arm down; it may touch the waist for a short stretch.
    connected = sorted((r for r, _, _ in arm_rows if r <= armpit), reverse=True)
    fingertips = armpit
    for row in connected:
        if fingertips - row > height * 0.08:
            break
        fingertips = row
    marks["armpit"], marks["fingertips"] = armpit, fingertips

    def arm_center(side, row):
        """Center of the arm at a height; side -1 is -x, +1 is +x."""
        best = min(arm_rows, key=lambda entry: abs(entry[0] - row))
        lo, hi = best[1] if side < 0 else best[2]
        cx = (lo + hi) / 2
        near = (np.abs(y - row) < band * 1.5) & (np.abs(x - cx) < (hi - lo) / 2 + band)
        return np.array([cx, row, np.median(z[near]) if near.any() else center_z])

    marks["arm_center"] = arm_center
    # Inner edge of each arm per height: vertices beyond it belong to that arm.
    marks["arm_edges"] = [(r, left[1], right[0]) for r, left, right in arm_rows]

    def leg_center(side, row):
        near = (np.abs(y - row) < band * 1.5) & (np.sign(x) == side)
        return np.array([np.median(x[near]), row, np.median(z[near])])

    marks["leg_center"] = leg_center
    toes = {}
    for side in (-1, 1):
        foot = (y < height * 0.06) & (np.sign(x) == side)
        tip = np.argmax(np.where(foot, z, -np.inf))
        toes[side] = np.array([x[tip], height * 0.02, z[tip] - height * 0.02])
    marks["toes"] = toes
    marks["center_z"] = center_z
    return marks


# MARK: - Fitting the skeleton

# Hip joints as a share of the distance from the middle to each thigh's center.
HIP_INSET = 0.62

def fit_joints(skeleton, marks, height):
    """World positions for every joint, in the mesh's space."""
    t = {}
    # Character's left is +x when facing +z; the template agrees if upperarm_l has x > 0.
    left = 1 if skeleton.position("upperarm_l")[0] > 0 else -1
    cz = marks["center_z"]
    neck_base = marks["neck"] - height * 0.035

    t["root"] = np.array([0, 0, cz])
    t["pelvis"] = np.array([0, marks["crotch"] + height * 0.07, cz])
    for i, name in enumerate(["spine_01", "spine_02", "spine_03"], start=1):
        y = t["pelvis"][1] + (neck_base - t["pelvis"][1]) * i / 4
        t[name] = np.array([0, y, cz])
    t["neck_01"] = np.array([0, neck_base, marks["front_z"](neck_base) - height * 0.05])
    head_y = marks["neck"] + height * 0.02
    t["Head"] = np.array([0, head_y, marks["front_z"](head_y) - height * 0.065])

    shoulder_y = marks["armpit"] + height * 0.045
    wrist_y = marks["fingertips"] + height * 0.075
    for side_name, side in (("l", left), ("r", -left)):
        shoulder = marks["arm_center"](side, marks["armpit"] - height * 0.01)
        shoulder[1] = shoulder_y
        wrist = marks["arm_center"](side, wrist_y)
        elbow = marks["arm_center"](side, (shoulder_y + wrist_y) / 2)
        t[f"clavicle_{side_name}"] = np.array([side * height * 0.025, shoulder_y - height * 0.01, cz])
        t[f"upperarm_{side_name}"] = shoulder
        t[f"lowerarm_{side_name}"] = elbow
        t[f"hand_{side_name}"] = wrist

        # The hip joint sits inside the pelvis, nearer the middle than the thigh's center;
        # placing it mid-thigh spreads the legs once an animation straightens them.
        hip = marks["leg_center"](side, marks["crotch"] - height * 0.04)
        hip[0] *= HIP_INSET
        hip[1] = marks["crotch"] + height * 0.03
        ankle = marks["leg_center"](side, height * 0.06)
        knee = marks["leg_center"](side, (hip[1] + ankle[1]) / 2 + height * 0.02)
        t[f"thigh_{side_name}"] = hip
        t[f"calf_{side_name}"] = knee
        t[f"foot_{side_name}"] = ankle
        t[f"ball_{side_name}"] = marks["toes"][side]
    return t, left


def build_skeleton(skeleton, targets):
    """New world rotations and positions for all joints. Bone orientations stay the
    template's (animation clips are authored for them), except limb bones, which are
    turned to point along the mesh's own limbs; their descendants turn with them."""
    rotation, position = {}, {}
    limb_chains = ["upperarm", "lowerarm", "thigh", "calf"]
    child_of = {"upperarm": "lowerarm", "lowerarm": "hand", "thigh": "calf", "calf": "foot"}
    turn = {}

    for name in skeleton.names:
        parent = skeleton.parent_name(name)
        base = name.rsplit("_", 1)
        prefix, side = (base[0], base[1]) if len(base) == 2 and base[1] in ("l", "r") else (name, None)
        template_r = skeleton.rotation(name)

        if prefix in limb_chains:
            child = f"{child_of[prefix]}_{side}"
            template_dir = skeleton.position(child) - skeleton.position(name)
            delta = rotation_between(template_dir, targets[child] - targets[name])
            turn[name] = delta
            rotation[name] = delta @ template_r
        elif parent in turn and prefix not in ("foot", "ball"):
            turn[name] = turn[parent]
            rotation[name] = turn[name] @ template_r
        else:
            rotation[name] = template_r

        if name in targets:
            position[name] = targets[name]
        else:
            # Fingers and leaf bones: the template's offset from the parent, turned with it.
            p = parent
            offset = skeleton.position(name) - skeleton.position(p)
            scale = 0.85
            position[name] = position[p] + turn.get(p, np.eye(3)) @ offset * scale
    return rotation, position


# MARK: - Skin weights

SEGMENT_END = {
    "pelvis": "spine_01", "spine_01": "spine_02", "spine_02": "spine_03", "spine_03": "neck_01",
    "neck_01": "Head", "clavicle_l": "upperarm_l", "clavicle_r": "upperarm_r",
    "upperarm_l": "lowerarm_l", "upperarm_r": "lowerarm_r", "lowerarm_l": "hand_l", "lowerarm_r": "hand_r",
    "thigh_l": "calf_l", "thigh_r": "calf_r", "calf_l": "foot_l", "calf_r": "foot_r",
    "foot_l": "ball_l", "foot_r": "ball_r",
}
# Radius (share of height) around each shoulder joint where arm and body weights blend.
SHOULDER_BLEND = 0.11


def skin_weights(points, skeleton, position, marks, height, left, hair, adjacency, colors):
    names = skeleton.names
    segments = {}
    for name in names:
        start = position[name]
        if name in SEGMENT_END:
            end = position[SEGMENT_END[name]]
        elif name == "Head":
            end = start + np.array([0, height * 0.14, 0])
        elif name.startswith("hand_"):
            end = start + (start - position[f"lowerarm_{name[-1]}"]) * 0.45
        elif name.startswith("ball_"):
            end = start + np.array([0, 0, height * 0.03])
        else:
            continue
        segments[name] = (start, end)

    x, y = points[:, 0], points[:, 1]

    def side_name(sign):
        return "l" if sign == left else "r"

    edge_rows = np.array([r for r, _, _ in marks["arm_edges"]])
    left_edge = np.array([e for _, e, _ in marks["arm_edges"]])
    right_edge = np.array([e for _, _, e in marks["arm_edges"]])
    nearest_row = np.argmin(np.abs(y[:, None] - edge_rows[None, :]), axis=1)
    below_armpit = y < marks["armpit"] + height * 0.01
    on_neg_arm = below_armpit & (x < left_edge[nearest_row]) & (y > marks["fingertips"] - height * 0.02)
    on_pos_arm = below_armpit & (x > right_edge[nearest_row]) & (y > marks["fingertips"] - height * 0.02)

    # Hands hang against the hips, so the arm region also catches trouser vertices.
    # Keep only what is connected to the upper arm through the arm region.
    # Where the elbow touches the waist the arm dips inside the torso's edge, so
    # vertices close to the arm bones may join too, if connected to the arm.
    elbow_y = (marks["armpit"] + marks["fingertips"]) / 2

    def arm(mask, sign):
        bones = [f"{b}_{side_name(sign)}" for b in ("upperarm", "lowerarm", "hand")]
        near = np.min([segment_distance(points, *segments[b]) for b in bones], axis=0) < height * 0.035
        allowed = (mask | (near & below_armpit)) & (y > marks["fingertips"] - height * 0.02)
        return connected_from(mask & (y > elbow_y), allowed, adjacency)

    on_neg_arm, on_pos_arm = arm(on_neg_arm, -1), arm(on_pos_arm, 1)
    # The shoulder cap (above the armpit, beyond the arm's inner edge there) moves with
    # the arm; left with the body, it stays behind when the arm rises.
    top = np.argmax(edge_rows)
    cap = (y >= marks["armpit"]) & (y < marks["neck"])
    on_neg_arm |= cap & (x < left_edge[top])
    on_pos_arm |= cap & (x > right_edge[top])

    # Bits of trousers fused onto the fingers: below the elbow, drop vertices colored
    # like the legs rather than the forearm, unless their neighbors are mostly arm.
    if colors is not None:
        thighs = (y < marks["crotch"]) & (y > marks["crotch"] - height * 0.12) & (np.abs(x) < height * 0.08)
        forearm = (on_neg_arm | on_pos_arm) & (y < elbow_y) & (y > marks["fingertips"] + height * 0.08)
        trouser_like = closer_to(colors, reference_color(colors, thighs), reference_color(colors, forearm)) & (y < elbow_y)
        for mask in (on_neg_arm, on_pos_arm):
            keep = mask & ~trouser_like
            share = neighbor_share(keep, adjacency)
            mask &= keep | (share > 0.5)
    on_legs = (y < marks["crotch"]) & ~on_neg_arm & ~on_pos_arm

    groups = {
        "arm-": ([f"{b}_{side_name(-1)}" for b in ("upperarm", "lowerarm", "hand")], on_neg_arm),
        "arm+": ([f"{b}_{side_name(1)}" for b in ("upperarm", "lowerarm", "hand")], on_pos_arm),
        "leg-": ([f"{b}_{side_name(-1)}" for b in ("thigh", "calf", "foot", "ball")], on_legs & (x < 0)),
        "leg+": ([f"{b}_{side_name(1)}" for b in ("thigh", "calf", "foot", "ball")], on_legs & (x >= 0)),
    }
    rest = ~(on_neg_arm | on_pos_arm | on_legs)
    groups["body"] = (["pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "Head",
                       "clavicle_l", "clavicle_r", "upperarm_l", "upperarm_r", "thigh_l", "thigh_r"], rest)

    # In the body group, arm bones only reach the shoulders and thigh bones only the
    # hips; otherwise a waistband next to the elbow would follow the arm.
    reach = {
        "upperarm_l": y > marks["armpit"] - height * 0.02, "upperarm_r": y > marks["armpit"] - height * 0.02,
        "thigh_l": y < marks["crotch"] + height * 0.08, "thigh_r": y < marks["crotch"] + height * 0.08,
        # Shoulder bones move the shoulders, not the chest.
        "clavicle_l": (np.abs(x) > height * 0.05) & (y > marks["armpit"] - height * 0.02),
        "clavicle_r": (np.abs(x) > height * 0.05) & (y > marks["armpit"] - height * 0.02),
    }

    # Foot bones only move the shoes: a trouser hem hanging over the shoe follows the leg.
    if colors is not None:
        toes = (y < height * 0.03) & (points[:, 2] > np.percentile(points[y < height * 0.03, 2], 85))
        shins = np.abs(y - height * 0.2) < height * 0.03
        toe_color, shin_color = reference_color(colors, toes), reference_color(colors, shins)
        if np.linalg.norm(toe_color - shin_color) < 0.1:
            print("barefoot: feet follow the foot bones below the ankle")
            shoe = y < height * 0.06
        else:
            shoe = closer_to(colors, toe_color, shin_color)
    else:
        shoe = y < height * 0.04
    for bone in ("foot_l", "foot_r", "ball_l", "ball_r"):
        reach[bone] = shoe

    joints = np.zeros((len(points), 4), np.uint8)
    weights = np.zeros((len(points), 4), np.float32)
    for group, (bones, mask) in groups.items():
        index = np.where(mask)[0]
        if not len(index):
            continue
        distances = np.stack([segment_distance(points[index], *segments[b]) for b in bones], axis=1)
        for column, bone in enumerate(bones):
            if bone in reach and (group == "body" or bone.startswith(("foot", "ball"))):
                distances[~reach[bone][index], column] = np.inf
        influence = 1 / (distances + height * 0.01) ** 4
        count = min(4, len(bones))
        order = np.argsort(-influence, axis=1)[:, :count]
        top = np.take_along_axis(influence, order, axis=1)
        top /= top.sum(axis=1, keepdims=True)
        joints[index, :count] = np.array([names.index(b) for b in bones])[order]
        weights[index, :count] = top

    group_of = np.zeros(len(points), int)
    for number, (_, mask) in enumerate(groups.values()):
        group_of[mask] = number
    # Hips and legs blend into each other; arms and the two legs stay apart.
    legs, body = [list(groups).index(g) for g in ("leg-", "leg+")], list(groups).index("body")
    joints, weights = smooth_weights(joints, weights, adjacency, group_of, len(names),
                                     blend=[(legs[0], body), (legs[1], body)])
    # Arms are weighted apart from the body (hands rest on the hips), which leaves a
    # seam at the shoulder; blend the two across a ball around each shoulder joint.
    shoulders = np.zeros(len(points), bool)
    for side in ("l", "r"):
        shoulders |= np.linalg.norm(points - position[f"upperarm_{side}"], axis=1) < height * SHOULDER_BLEND
    joints, weights = relax_weights(joints, weights, adjacency, shoulders, len(names))

    # Hair and the head move with the head bone; the neck fades from its own
    # weights into the head's, so the skin there bends instead of tearing.
    head_bone = names.index("Head")
    start, end = marks["neck"] - height * 0.015, marks["neck"] + height * 0.04
    fade = np.clip((y - start) / (end - start), 0, 1)
    fade = fade * fade * (3 - 2 * fade)
    fade[hair] = 1
    dense = np.zeros((len(points), len(names)), np.float32)
    for k in range(4):
        np.add.at(dense, (np.arange(len(points)), joints[:, k]), weights[:, k])
    dense *= (1 - fade)[:, None]
    dense[:, head_bone] += fade
    order = np.argsort(-dense, axis=1)[:, :4]
    top = np.take_along_axis(dense, order, axis=1)
    joints, weights = order.astype(np.uint8), (top / top.sum(axis=1, keepdims=True)).astype(np.float32)
    head = fade > 0.5

    parts = {name: mask & ~head for name, (_, mask) in groups.items()}
    parts["head"] = head
    return joints, weights, parts


def vertex_colors(gltf, binary, primitive, uvs):
    """Base color texture sampled at each vertex, or None without a texture."""
    material = gltf["materials"][primitive["material"]]
    info = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
    if not info:
        return None
    source = gltf["textures"][info["index"]]["source"]
    texture = np.asarray(Image.open(io.BytesIO(image_bytes(gltf, binary, source))).convert("RGB"), np.float32) / 255
    th, tw, _ = texture.shape
    return texture[np.clip((uvs[:, 1] * (th - 1)).astype(int), 0, th - 1), np.clip((uvs[:, 0] * (tw - 1)).astype(int), 0, tw - 1)]


def reference_color(colors, mask):
    return np.median(colors[mask], axis=0)


def closer_to(colors, a, b):
    """Vertices whose color is nearer reference a than reference b."""
    return np.linalg.norm(colors - a, axis=1) < np.linalg.norm(colors - b, axis=1)


def hair_mask(colors, points, marks, height):
    """Hair-colored vertices around the head and shoulders, so strands there follow
    the head instead of the arms. Compared against the chest's color too: a dark
    top can be close to dark hair."""
    if colors is None:
        return np.zeros(len(points), bool)
    x, y, z = points[:, 0], points[:, 1], points[:, 2]
    crown = y > y.max() - height * 0.04
    chest_rows = np.abs(y - (marks["armpit"] + height * 0.02)) < height * 0.02
    chest = chest_rows & (np.abs(x) < height * 0.05) & (z > np.percentile(z[chest_rows], 80))
    hair_color, chest_color = reference_color(colors, crown), reference_color(colors, chest)
    # A bald head: the crown is the same color as the face.
    face_rows = np.abs(y - marks["nose"]) < height * 0.02
    face = face_rows & (np.abs(x) < height * 0.03) & (z > np.percentile(z[face_rows], 90))
    if np.linalg.norm(hair_color - reference_color(colors, face)) < 0.1:
        print("bald: no hair to pin")
        return np.zeros(len(points), bool)
    is_hair = closer_to(colors, hair_color, chest_color) & (np.linalg.norm(colors - hair_color, axis=1) < 0.12)
    return is_hair & (y > marks["armpit"] - height * 0.06)


def cut_bridges(indices, points, parts, marks, height):
    """Drops triangles that join parts moving independently. Generated meshes fuse
    whatever touches (a hand resting on the jeans, two trouser hems, hair on the
    shoulders); those triangles are invisible at rest but stretch into sheets once
    the parts move apart. Real joints (shoulders, hips, neck) stay connected."""
    names = list(parts)
    label = np.zeros(len(points), int)
    for number, name in enumerate(names):
        label[parts[name]] = number
    triangles = indices.reshape(-1, 3)
    a, b, c = (label[triangles[:, k]] for k in range(3))
    y = points[triangles, 1].mean(axis=1)

    def joins(p, q):
        i, j = names.index(p), names.index(q)
        return ((a == i) | (b == i) | (c == i)) & ((a == j) | (b == j) | (c == j))

    shoulder = y > marks["armpit"] - height * 0.03
    # The head joins the body only at the neck itself, not where hair rests on the shoulders.
    neck = (y > marks["neck"] - height * 0.02) & (np.abs(points[triangles, 0].mean(axis=1)) < height * 0.05)
    cut = (joins("arm-", "leg-") | joins("arm-", "leg+") | joins("arm+", "leg-") | joins("arm+", "leg+")
           | joins("leg-", "leg+") | joins("arm-", "arm+")
           | ((joins("arm-", "body") | joins("arm+", "body")) & ~shoulder)
           | ((joins("head", "body") | joins("head", "arm-") | joins("head", "arm+")) & ~neck))
    print(f"cut {int(cut.sum())} bridging triangles")
    return triangles[~cut].ravel()


# MARK: - Main

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mesh")
    parser.add_argument("template")
    parser.add_argument("output")
    parser.add_argument("--forward", default="+z", choices=["+x", "-x", "+z", "-z"])
    parser.add_argument("--name", default="Generated_Body")
    args = parser.parse_args()

    gltf, binary = read_glb(args.mesh)
    primitive = gltf["meshes"][0]["primitives"][0]
    points = accessor(gltf, binary, primitive["attributes"]["POSITION"]).astype(np.float64)
    normals = accessor(gltf, binary, primitive["attributes"]["NORMAL"]).astype(np.float64)
    uvs = accessor(gltf, binary, primitive["attributes"]["TEXCOORD_0"]).astype(np.float32)
    indices = accessor(gltf, binary, primitive["indices"]).astype(np.uint32)

    # Face +z, stand on y = 0, match the template's height.
    turn = {"+z": np.eye(3), "-z": np.diag([-1, 1, -1]),
            "+x": np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]]), "-x": np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]])}[args.forward]
    points, normals = points @ turn.T, normals @ turn.T

    template_gltf, template_binary = read_glb(args.template)
    skeleton = Skeleton(template_gltf, template_binary)
    template_y = np.concatenate([
        accessor(template_gltf, template_binary, p["attributes"]["POSITION"])[:, 1]
        for mesh in template_gltf["meshes"] for p in mesh["primitives"]
    ])
    height = float(template_y.max() - template_y.min())

    points *= height / (points[:, 1].max() - points[:, 1].min())
    points[:, 1] -= points[:, 1].min()
    points[:, [0, 2]] -= np.median(points[points[:, 1] < height * 0.5][:, [0, 2]], axis=0)

    assert np.isfinite(points).all() and np.isfinite(normals).all(), "non-finite mesh data"
    marks = measure(surface_samples(points, indices, 400_000), height)
    targets, left = fit_joints(skeleton, marks, height)
    rotation, position = build_skeleton(skeleton, targets)
    colors = vertex_colors(gltf, binary, primitive, uvs)
    hair = hair_mask(colors, points, marks, height)
    adjacency = vertex_adjacency(indices, points)
    joints, weights, parts = skin_weights(points, skeleton, position, marks, height, left, hair, adjacency, colors)
    indices = cut_bridges(indices, points, parts, marks, height)
    assert np.isfinite(weights).all() and np.allclose(weights.sum(axis=1), 1, atol=1e-3), "bad weights"
    print("landmarks:", {k: round(float(v) / height, 3) for k, v in marks.items() if isinstance(v, float) or isinstance(v, np.floating)})

    # Textures: base color 1024, normal 512, no occlusion/roughness map.
    material = gltf["materials"][primitive["material"]]
    pbr = material.get("pbrMetallicRoughness", {})

    def texture_image(info, size):
        source = gltf["textures"][info["index"]]["source"]
        image = Image.open(io.BytesIO(image_bytes(gltf, binary, source))).convert("RGB")
        image.thumbnail((size, size))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=88)
        return out.getvalue()

    writer = GlbWriter()
    images, textures = [], []
    for info, size in ((pbr.get("baseColorTexture"), 1024), (material.get("normalTexture"), 512)):
        if info:
            images.append({"bufferView": writer.view(texture_image(info, size)), "mimeType": "image/jpeg"})
            textures.append({"source": len(images) - 1})
    out_material = {"name": f"MI_{args.name}", "doubleSided": True,
                    "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0, "roughnessFactor": 1}}
    if len(textures) > 1:
        out_material["normalTexture"] = {"index": 1}

    index_type, index_component = (np.uint16, 5123) if len(points) < 65536 else (np.uint32, 5125)
    attributes = {
        "POSITION": writer.accessor(points.astype(np.float32), "VEC3", 5126, 34962, bounds=True),
        "NORMAL": writer.accessor((normals / np.linalg.norm(normals, axis=1, keepdims=True)).astype(np.float32), "VEC3", 5126, 34962),
        "TEXCOORD_0": writer.accessor(uvs, "VEC2", 5126, 34962),
        "JOINTS_0": writer.accessor(joints, "VEC4", 5121, 34962),
        "WEIGHTS_0": writer.accessor(weights, "VEC4", 5126, 34962),
    }
    indices_accessor = writer.accessor(indices.astype(index_type), "SCALAR", index_component, 34963)

    # Nodes: Armature -> [mesh, root -> ... bones], bones in the template's order.
    nodes = [{"name": "Armature", "children": []}, {"name": args.name, "mesh": 0, "skin": 0}]
    node_of = {}
    for name in skeleton.names:
        node_of[name] = len(nodes)
        nodes.append({"name": name})
    world = {}
    for name in skeleton.names:
        parent = skeleton.parent_name(name)
        r, t = rotation[name], position[name]
        if parent:
            pr, pt = rotation[parent], position[parent]
            local_r, local_t = pr.T @ r, pr.T @ (t - pt)
            nodes[node_of[parent]].setdefault("children", []).append(node_of[name])
        else:
            local_r, local_t = r, t
            nodes[0]["children"].append(node_of[name])
        nodes[node_of[name]]["rotation"] = matrix_to_quat(local_r).tolist()
        nodes[node_of[name]]["translation"] = local_t.tolist()
        world[name] = (r, t)
    nodes[0]["children"].insert(0, 1)

    inverse_binds = []
    for name in skeleton.names:
        r, t = world[name]
        m = np.eye(4)
        m[:3, :3], m[:3, 3] = r, t
        inverse_binds.append(np.linalg.inv(m).T.flatten())
    binds_accessor = writer.accessor(np.array(inverse_binds, np.float32), "MAT4", 5126)

    out = {
        "asset": {"version": "2.0", "generator": "AvatarKit rig-generated-body.py"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes,
        "meshes": [{"name": args.name, "primitives": [{"attributes": attributes, "indices": indices_accessor, "material": 0}]}],
        "skins": [{"name": "Armature", "joints": [node_of[n] for n in skeleton.names], "inverseBindMatrices": binds_accessor}],
        "materials": [out_material], "textures": textures, "images": images,
        "samplers": [{}],
    }
    for texture in textures:
        texture["sampler"] = 0
    writer.write(args.output, out)
    print(f"wrote {args.output}: {len(points)} vertices, {len(indices) // 3} triangles, {len(skeleton.names)} joints")


if __name__ == "__main__":
    main()
