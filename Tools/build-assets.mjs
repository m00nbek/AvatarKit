// Turns the generated characters (Tools/tripo) and the Quaternius animation packs
// into the small GLBs the app ships, and writes catalog.json describing them.
//
//   ./fetch-assets.sh && npm install && npm run build-assets
//
// Sources are read from ../.assets-source, output goes to
// ../Sources/AvatarKit/Resources/Avatar.
//
// One base body per gender (Tools/tripo/assets.json `bodies`), rigged to the
// Quaternius skeleton so the animation packs play on it. Outfits generated on the
// base (Hunyuan on our pod, or Tripo) are cut into items that fit it
// (fit-other-generators.py); garments made on their own are fitted onto it
// (fit-garments.py). The base generated again with other builds becomes its body
// types (add-body-shapes.py), which every item follows.

import { NodeIO } from '@gltf-transform/core';
import { dedup, prune, resample } from '@gltf-transform/functions';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const here = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = path.resolve(here, '../.assets-source');
const OUT = path.resolve(here, '../Sources/AvatarKit/Resources/Avatar');
const GENERATED = path.join(SOURCE, 'Tripo/gen');
const io = new NodeIO();
const manifest = JSON.parse(fs.readFileSync(path.join(here, 'tripo/assets.json'), 'utf8'));

// The paid source version of Animation Library 1 has all 120 clips (the free one 46) and
// already uses Unreal bone names; the free one is the fallback.
const UAL1_SOURCE = 'Universal Animation Library Source/Universal Animation Library[Source]/Unreal-Godot/UAL1.glb';
const UAL1 = 'Universal Animation Library/Animation Library[Standard]/Godot/AnimationLibrary_Godot_Standard.glb';
const UAL2 = 'Universal Animation Library 2/Universal Animation Library 2 [Standard]/Unreal-Godot/UAL2_Standard.glb';
// The Quaternius base characters are only a skeleton template now: generated bodies are
// rigged to their bones, which the animation packs drive.
const TEMPLATE_DIR = 'Universal Base Characters/Universal Base Characters[Standard]/Base Characters/Godot - UE';
const TEMPLATES = { female: 'Superhero_Female_FullBody.gltf', male: 'Superhero_Male_FullBody.gltf' };

const BASE_COLOR_SIZE = 1024;
const NORMAL_SIZE = 512;
const HIPS = 'pelvis';
// The app's order; `normal` is the base itself, the others are its morph targets.
const BODY_TYPES = ['verySkinny', 'skinny', 'normal', 'heavy'];
// Hunyuan's models (Tools/clothes/make_item.py): the cutter tries each facing and keeps the best fit.
const HUNYUAN_FORWARD = 'auto';

// MARK: - Skeleton

// Animation Library 1 uses Rigify names; everything else uses Unreal names.
// The rest poses are identical, so renaming is enough.
const RIGIFY_TO_UNREAL = (() => {
  const map = {
    'DEF-hips': 'pelvis', 'DEF-spine.001': 'spine_01', 'DEF-spine.002': 'spine_02',
    'DEF-spine.003': 'spine_03', 'DEF-neck': 'neck_01', 'DEF-head': 'Head',
  };
  for (const [side, s] of [['L', 'l'], ['R', 'r']]) {
    Object.assign(map, {
      [`DEF-shoulder.${side}`]: `clavicle_${s}`, [`DEF-upper_arm.${side}`]: `upperarm_${s}`,
      [`DEF-forearm.${side}`]: `lowerarm_${s}`, [`DEF-hand.${side}`]: `hand_${s}`,
      [`DEF-thigh.${side}`]: `thigh_${s}`, [`DEF-shin.${side}`]: `calf_${s}`,
      [`DEF-foot.${side}`]: `foot_${s}`, [`DEF-toe.${side}`]: `ball_${s}`,
    });
    for (const finger of ['index', 'middle', 'pinky', 'ring']) {
      for (const i of [1, 2, 3]) map[`DEF-f_${finger}.0${i}.${side}`] = `${finger}_0${i}_${s}`;
    }
    for (const i of [1, 2, 3]) map[`DEF-thumb.0${i}.${side}`] = `thumb_0${i}_${s}`;
  }
  return map;
})();

// MARK: - Document helpers

function renameNodes(doc, map) {
  for (const node of doc.getRoot().listNodes()) {
    const renamed = map[node.getName()];
    if (renamed) node.setName(renamed);
  }
}

// Disposing an animation leaves its samplers holding the keyframe data.
function disposeAnimation(animation) {
  animation.listSamplers().forEach((sampler) => sampler.dispose());
  animation.listChannels().forEach((channel) => channel.dispose());
  animation.dispose();
}

function keepAnimations(doc, names) {
  const found = new Set();
  for (const animation of doc.getRoot().listAnimations()) {
    if (names.includes(animation.getName())) found.add(animation.getName());
    else disposeAnimation(animation);
  }
  const missing = names.filter((name) => !found.has(name));
  if (missing.length) throw new Error(`Missing clips: ${missing.join(', ')}`);
}

// The app only uses rotations plus hip movement, so drop the rest.
function stripChannels(doc) {
  for (const animation of doc.getRoot().listAnimations()) {
    for (const channel of animation.listChannels()) {
      const pathName = channel.getTargetPath();
      const isHipMove = pathName === 'translation' && channel.getTargetNode()?.getName() === HIPS;
      if (pathName !== 'rotation' && !isHipMove) {
        channel.getSampler()?.dispose();
        channel.dispose();
      }
    }
  }
}

function removeMeshes(doc) {
  for (const node of doc.getRoot().listNodes()) {
    node.setMesh(null);
    node.setSkin(null);
  }
}

// Some source .gltf files point at texture names that aren't in the pack
// (`T_Hair_1_Normal_png.png` next to `T_Hair_1_Normal.png`), so images are
// resolved by name across the whole pack.
async function readSource(file) {
  if (file.endsWith('.glb')) return io.read(file);
  const dir = path.dirname(file);
  const json = JSON.parse(fs.readFileSync(file, 'utf8'));
  const resources = {};
  for (const buffer of json.buffers ?? []) {
    resources[buffer.uri] = fs.readFileSync(path.join(dir, decodeURIComponent(buffer.uri)));
  }
  for (const image of json.images ?? []) {
    if (image.uri) resources[image.uri] = fs.readFileSync(findImage(dir, decodeURIComponent(image.uri)));
  }
  return io.readJSON({ json, resources });
}

function findImage(dir, name) {
  const names = [name, name.replace(/_png\.png$/i, '.png')];
  for (const candidate of names) {
    if (fs.existsSync(path.join(dir, candidate))) return path.join(dir, candidate);
  }
  const packRoot = path.join(SOURCE, path.relative(SOURCE, dir).split(path.sep)[0]);
  const match = fs.readdirSync(packRoot, { recursive: true })
    .find((entry) => names.includes(path.basename(entry)));
  if (!match) throw new Error(`Image ${name} not found for ${dir}`);
  return path.join(packRoot, match);
}

// Base color 1024 JPEG, normals 512, no roughness/occlusion (the app shades
// matte). Returns the mean luminance of each material's base color, which the
// app uses to recolor textures without changing their brightness.
async function shrinkTextures(doc) {
  const luminance = {};
  for (const material of doc.getRoot().listMaterials()) {
    material.setMetallicRoughnessTexture(null);
    material.setOcclusionTexture(null);
    material.setEmissiveTexture(null);
  }
  for (const texture of doc.getRoot().listTextures()) {
    const users = texture.listParents().filter((p) => p.propertyType === 'Material');
    const isNormal = users.some((m) => m.getNormalTexture() === texture);
    const isBaseColor = users.some((m) => m.getBaseColorTexture() === texture);
    if (!isNormal && !isBaseColor) continue;
    const size = isNormal && !isBaseColor ? NORMAL_SIZE : BASE_COLOR_SIZE;
    const image = await sharp(Buffer.from(texture.getImage()))
      .removeAlpha()
      .resize(size, size, { fit: 'inside', withoutEnlargement: true })
      .jpeg({ quality: isNormal ? 92 : 85 })
      .toBuffer();
    texture.setImage(new Uint8Array(image)).setMimeType('image/jpeg')
      .setURI(texture.getURI().replace(/\.png$/i, '.jpg'));
    if (isBaseColor) {
      const { channels } = await sharp(image).stats();
      const value = 0.299 * channels[0].mean + 0.587 * channels[1].mean + 0.114 * channels[2].mean;
      for (const material of users) luminance[material.getName()] = Number((value / 255).toFixed(3));
    }
  }
  return luminance;
}

async function write(doc, to) {
  await doc.transform(prune(), dedup());
  const target = path.join(OUT, to);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  await io.write(target, doc);
  console.log(`${to.padEnd(36)} ${(fs.statSync(target).size / 1024).toFixed(0).padStart(6)} KB`);
}

const source = (file) => path.join(SOURCE, file);
const luminance = {};
const run = (command, args) => execFileSync(command, args, { stdio: 'inherit' });

// MARK: - Bodies

/// The Quaternius character for a gender, saved where the rigging script reads it.
async function skeletonTemplate(gender) {
  const file = path.join(GENERATED, 'templates', `${gender}.glb`);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  await io.write(file, await readSource(source(`${TEMPLATE_DIR}/${TEMPLATES[gender]}`)));
  return file;
}

/// A copy of a model with vertex normals, which the cutter needs; Hunyuan's OBJ to GLB step can leave them out.
async function withNormals(model) {
  const prepared = model.replace(/\.glb$/, '.prepared.glb');
  const doc = await io.read(model);
  for (const primitive of doc.getRoot().listMeshes().flatMap((mesh) => mesh.listPrimitives())) {
    if (primitive.getAttribute('NORMAL')) continue;
    const position = primitive.getAttribute('POSITION');
    const p = position.getArray();
    const index = primitive.getIndices().getArray();
    const n = new Float32Array(p.length);
    // Each triangle's normal, weighted by its area, added to its corners; then normalized.
    for (let t = 0; t < index.length; t += 3) {
      const [a, b, c] = [index[t] * 3, index[t + 1] * 3, index[t + 2] * 3];
      const u = [p[b] - p[a], p[b + 1] - p[a + 1], p[b + 2] - p[a + 2]];
      const v = [p[c] - p[a], p[c + 1] - p[a + 1], p[c + 2] - p[a + 2]];
      const f = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
      for (const corner of [a, b, c]) for (let k = 0; k < 3; k++) n[corner + k] += f[k];
    }
    for (let i = 0; i < n.length; i += 3) {
      const length = Math.hypot(n[i], n[i + 1], n[i + 2]) || 1;
      n[i] /= length; n[i + 1] /= length; n[i + 2] /= length;
    }
    primitive.setAttribute('NORMAL', doc.createAccessor().setType('VEC3').setArray(n).setBuffer(position.getBuffer()));
  }
  await io.write(prepared, doc);
  return prepared;
}

/// A generated base body, its items and faces. Null when the base hasn't been generated yet.
async function buildBody(body) {
  const base = path.join(GENERATED, body.base.id, 'model.glb');
  if (!fs.existsSync(base)) {
    console.warn(`skip ${body.gender} body: ${path.relative(SOURCE, base)} not found`);
    return null;
  }
  const { forward } = manifest;
  const simplified = base.replace(/\.glb$/, '.simplified.glb');
  const rigged = base.replace(/\.glb$/, '.rigged.glb');
  run('node', [path.join(here, 'simplify-mesh.mjs'), base, simplified, '30000']);
  run('python3', [path.join(here, 'rig-generated-body.py'), simplified, await skeletonTemplate(body.gender), rigged,
    '--forward', forward, '--name', body.name]);
  // The flat front and back renders that garment pictures are drawn on (generate.mjs garment).
  run('python3', [path.join(here, 'render-body.py'), rigged, path.join(GENERATED, 'renders', body.gender)]);

  const outfits = [];
  for (const outfit of body.outfits) {
    const model = path.join(GENERATED, outfit.id, 'model.glb');
    if (!fs.existsSync(model)) continue;
    if ((outfit.generator ?? manifest.clothesGenerator) === 'hunyuan') {
      // Hunyuan's models are light already, and its atlas of small UV islands smears if reduced.
      outfits.push({ model: await withNormals(model), forward: HUNYUAN_FORWARD, otherGenerator: true, items: outfit.items });
      continue;
    }
    const reduced = model.replace(/\.glb$/, '.simplified.glb');
    // Cut from the full-resolution outfit: reducing first blurs the edges where clothes meet skin.
    run('node', [path.join(here, 'simplify-mesh.mjs'), model, reduced, '150000']);
    outfits.push({ model: reduced, forward, items: outfit.items });
  }
  for (const garment of body.garments ?? []) {
    const model = path.join(GENERATED, garment.id, 'model.glb');
    if (!fs.existsSync(model)) continue;
    // The front picture the garment was made from, to find which way it faces.
    const picture = garment.redraw === false ? path.join(SOURCE, garment.pictures.front)
      : path.join(GENERATED, garment.sameViewsAs ?? garment.id, 'front.png');
    outfits.push({
      model: garment.generator === 'hunyuan' ? await withNormals(model) : model, garment: true, forward: 'auto',
      picture, items: [garment.item],
    });
  }
  const fitted = path.join(GENERATED, 'fitted', body.gender);
  fs.rmSync(fitted, { recursive: true, force: true });
  fs.mkdirSync(fitted, { recursive: true });
  fs.writeFileSync(path.join(fitted, 'outfits.json'), JSON.stringify(outfits, null, 2));
  // Cuts Tripo's outfits exactly as fit-outfits.py does, adjusts for Hunyuan's (fit-other-generators.py)
  // and fits garments (fit-garments.py).
  run('python3', [path.join(here, 'fit-garments.py'), rigged, path.join(fitted, 'outfits.json'), fitted]);

  const shapes = manifest.bodyShapes
    .map((shape) => ({ ...shape, model: path.join(GENERATED, `${body.base.id}_${shape.id}`, 'model.glb') }))
    .filter((shape) => fs.existsSync(shape.model));
  if (shapes.length) {
    run('python3', [path.join(here, 'add-body-shapes.py'), rigged, fitted,
      ...shapes.map((shape) => `${shape.id}=${shape.model}`), '--forward', forward]);
  }

  const bodyDoc = await io.read(path.join(fitted, 'base.glb'));
  Object.assign(luminance, await shrinkTextures(bodyDoc));
  const bodyFile = `Bodies/${body.gender}.glb`;
  await write(bodyDoc, bodyFile);

  const items = [];
  for (const item of outfits.flatMap((o) => o.items)) {
    const doc = await io.read(path.join(fitted, `${item.id}.glb`));
    await shrinkTextures(doc);
    const file = `Items/${item.id}.glb`;
    await write(doc, file);
    items.push({
      id: item.id, name: item.name, slot: item.slot, file, genders: [body.gender],
      tintMaterials: [], skinMaterials: [], hides: [], hidesSlots: [],
    });
  }
  // Faces: Tripo re-textured the base mesh; bake each onto the base's own texture layout.
  const faces = [];
  for (const face of body.faces) {
    const retextured = path.join(GENERATED, face.id, 'model.glb');
    if (!fs.existsSync(retextured)) continue;
    const texture = `Bodies/${body.gender}_${face.id}.jpg`;
    fs.mkdirSync(path.dirname(path.join(OUT, texture)), { recursive: true });
    run('python3', [path.join(here, 'bake-texture.py'), rigged, retextured, path.join(OUT, texture), '--forward', forward]);
    faces.push({ id: face.id, name: face.name, texture });
  }

  const defaultOutfit = body.outfits.find((o) => o.id === body.defaultOutfit);
  const built = new Set(items.map((item) => item.id));
  return {
    body: {
      gender: body.gender, file: bodyFile,
      bodyTypes: BODY_TYPES.filter((type) => type === 'normal' || shapes.some((shape) => shape.id === type)),
      skinMaterials: [], hairMaterials: [], faces, faceMaterials: [`MI_${body.name}`],
    },
    items,
    default: {
      config: {
        gender: body.gender, bodyType: 'normal', skinTone: body.skinTone,
        items: Object.fromEntries((defaultOutfit?.items ?? []).filter((item) => built.has(item.id))
          .map((item) => [item.slot, { itemID: item.id }])),
      },
    },
  };
}

// MARK: - Moves

// Clips only the source version of Animation Library 1 has; the moves using them are left out without it.
const SOURCE_ONLY = [
  { move: 'walk', clip: 'Walk_Loop', loops: true },
  { move: 'catwalk', clip: 'Walk_Formal_Loop', loops: true },
  { move: 'celebrate', clip: 'Celebration', loops: false },
  { move: 'talk', clip: 'Idle_Talking_Loop', loops: true },
  { move: 'lookAround', clip: 'Idle_LookAround_Loop', loops: true },
  { move: 'sit', clip: 'GroundSit_Idle_Loop', loops: true },
  { move: 'backflip', clip: 'BackFlip', loops: false },
  { move: 'cry', clip: 'Crying', loops: false },
];

async function buildMoves() {
  const hasSource = fs.existsSync(source(UAL1_SOURCE));
  const library1 = hasSource
    ? { from: UAL1_SOURCE, clips: ['Idle_Loop', 'Dance_Loop', ...SOURCE_ONLY.map((m) => m.clip)] }
    : { from: UAL1, rename: RIGIFY_TO_UNREAL, clips: ['Idle_Loop', 'Dance_Loop'] };
  const jobs = [
    { ...library1, to: 'Moves/moves_library1.glb' },
    { from: UAL2, to: 'Moves/moves_library2.glb', clips: ['Yes', 'Idle_No_Loop', 'Idle_FoldArms_Loop', 'Idle_TalkingPhone_Loop'] },
  ];
  for (const job of jobs) {
    const doc = await io.read(source(job.from));
    if (job.rename) renameNodes(doc, job.rename);
    keepAnimations(doc, job.clips);
    stripChannels(doc);
    removeMeshes(doc);
    await doc.transform(resample());
    await write(doc, job.to);
  }
  return [
    { move: 'idle', file: 'Moves/moves_library1.glb', clip: 'Idle_Loop', loops: true },
    { move: 'wave', loops: false },
    { move: 'yes', file: 'Moves/moves_library2.glb', clip: 'Yes', loops: false },
    { move: 'no', file: 'Moves/moves_library2.glb', clip: 'Idle_No_Loop', loops: false },
    { move: 'dance', file: 'Moves/moves_library1.glb', clip: 'Dance_Loop', loops: true },
    { move: 'crossArms', file: 'Moves/moves_library2.glb', clip: 'Idle_FoldArms_Loop', loops: true },
    { move: 'phone', file: 'Moves/moves_library2.glb', clip: 'Idle_TalkingPhone_Loop', loops: true },
    ...(hasSource ? SOURCE_ONLY.map((m) => ({ ...m, file: 'Moves/moves_library1.glb' })) : []),
  ];
}

// MARK: - Catalog

fs.rmSync(OUT, { recursive: true, force: true });

const built = [];
for (const body of manifest.bodies) {
  const result = await buildBody(body);
  if (result) built.push(result);
}
if (!built.length) throw new Error('No base body generated yet (Tools/tripo/generate.mjs)');
const moves = await buildMoves();

const catalog = {
  rig: {
    hips: 'pelvis', head: 'Head', leftUpperArm: 'upperarm_l', rightUpperArm: 'upperarm_r',
    rightForearm: 'lowerarm_r', rightHand: 'hand_r', rightIndex: 'index_01_r', rightPinky: 'pinky_01_r',
  },
  bodies: built.map((b) => b.body),
  items: built.flatMap((b) => b.items),
  moves,
  materialLuminance: luminance,
  skinTones: ['#F6D3B8', '#EBB894', '#D99A6C', '#B9784B', '#8D5524', '#5C3A21'],
  hairColors: ['#1C1A19', '#3B2A20', '#6A4530', '#A7744A', '#D9B26F', '#B5452E', '#9C9C9C', '#5B6DD9'],
  palette: ['#2B2F36', '#F2F2EE', '#C0392B', '#E67E22', '#F1C40F', '#27AE60', '#2E86DE', '#8E44AD', '#E84393', '#7F5539'],
  defaults: built.map((b) => b.default),
};

fs.writeFileSync(path.join(OUT, 'catalog.json'), JSON.stringify(catalog, null, 2) + '\n');
console.log(`catalog.json: ${catalog.bodies.length} bodies (${catalog.bodies.map((b) => `${b.gender}: ${b.bodyTypes.join(', ')}`).join('; ')}), `
  + `${catalog.items.length} items, ${moves.length} moves`);
