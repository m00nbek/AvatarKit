// Generates the avatar assets listed in assets.json with the Tripo API.
//
//   node generate.mjs balance
//   node generate.mjs make <id>        every step an asset needs, in order (then run build-assets)
//   node generate.mjs image <id>       edited image: base body, body type, outfit or face
//   node generate.mjs multiview <id>   four views of that image
//   node generate.mjs model <id>       3D model from the four views
//   node generate.mjs segment <id>     split an outfit's model into parts
//   node generate.mjs retexture <id>   face: new texture on the base body
//   node generate.mjs clothes <id>...  outfits made on our Hunyuan pod: dressed images, then 3D
//   node generate.mjs garment <id>...  garments made on their own: ghost-mannequin pictures, then 3D
//   node generate.mjs status
//
// IDs: a body's base (base_female), its body types (base_female_heavy: base ID + "_" +
// a bodyShapes ID), its outfits and its faces.
//
// Every task ID is saved as soon as it's created, so a rerun picks up the same
// task instead of paying again. Add --force to redo a step.
// The key is read from TRIPO_API_KEY or Tools/tripo/.env.

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
// Global region; the China region is openapi.tripo3d.com. Keys only work in their own region.
const API = process.env.TRIPO_API_BASE ?? 'https://openapi.tripo3d.ai/v3';
const OUT = path.resolve(here, '../../.assets-source/Tripo/gen');
const STATE = path.join(OUT, 'state.json');
const manifest = JSON.parse(fs.readFileSync(path.join(here, 'assets.json'), 'utf8'));

function apiKey() {
  if (process.env.TRIPO_API_KEY) return process.env.TRIPO_API_KEY;
  const env = path.join(here, '.env');
  const line = fs.existsSync(env) && fs.readFileSync(env, 'utf8').split('\n').find((l) => l.startsWith('TRIPO_API_KEY='));
  if (!line) throw new Error('Set TRIPO_API_KEY or add it to Tools/tripo/.env');
  return line.slice('TRIPO_API_KEY='.length).trim();
}

const headers = () => ({ Authorization: `Bearer ${apiKey()}` });

async function call(method, endpoint, body) {
  const response = await fetch(API + endpoint, {
    method,
    headers: body instanceof FormData ? headers() : { ...headers(), 'Content-Type': 'application/json' },
    body: body instanceof FormData ? body : body && JSON.stringify(body),
  });
  const json = await response.json().catch(() => ({}));
  if (!response.ok || json.code !== 0) {
    throw new Error(`${method} ${endpoint}: HTTP ${response.status} ${JSON.stringify(json).slice(0, 400)}`);
  }
  return json.data;
}

// MARK: - State

const state = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, 'utf8')) : {};
function save() {
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(STATE, JSON.stringify(state, null, 2));
}
const entry = (id) => (state[id] ??= {});

async function upload(file) {
  const form = new FormData();
  form.append('file', new Blob([fs.readFileSync(file)]), path.basename(file));
  return (await call('POST', '/files', form)).file_token;
}

async function wait(taskId, label) {
  let last = '';
  for (;;) {
    const task = await call('GET', `/tasks/${taskId}`);
    const line = `${label}: ${task.status} ${task.progress ?? 0}%`;
    if (line !== last) console.log(line);
    last = line;
    if (task.status === 'success') return task;
    if (['failed', 'banned', 'expired', 'cancelled'].includes(task.status)) {
      throw new Error(`${label} ${task.status}: ${task.error_code ?? ''} ${task.error_message ?? ''}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 4000));
  }
}

/// Finds a field anywhere in a task's output; some task types nest it a level deeper than documented.
function pick(output, ...keys) {
  if (!output || typeof output !== 'object') return undefined;
  for (const key of keys) if (typeof output[key] === 'string') return output[key];
  for (const value of Object.values(output)) {
    const found = pick(value, ...keys);
    if (found) return found;
  }
  return undefined;
}

async function download(url, file) {
  if (!url) throw new Error(`No download URL for ${path.basename(file)}`);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`download ${url}: HTTP ${response.status}`);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, Buffer.from(await response.arrayBuffer()));
  return path.relative(OUT, file);
}

/// Creates the task once, then waits for it; reruns resume the saved task.
async function step(id, name, create, collect) {
  const record = entry(id);
  if (record[name]?.done && !process.argv.includes('--force')) {
    console.log(`${id} ${name}: already done`, record[name].files ?? '');
    return record[name];
  }
  if (!record[name]?.task || process.argv.includes('--force')) {
    record[name] = { task: await create() };
    save();
  }
  const task = await wait(record[name].task, `${id} ${name}`);
  record[name] = { task: record[name].task, done: true, credits: task.credits_consumed, files: await collect(task.output) };
  save();
  console.log(`${id} ${name}: done, ${task.credits_consumed ?? '?'} credits`, record[name].files);
  return record[name];
}

// MARK: - Assets

function find(id) {
  for (const body of manifest.bodies) {
    if (id === body.base.id) return { kind: 'base', body, ...body.base };
    const shape = manifest.bodyShapes.find((s) => id === `${body.base.id}_${s.id}`);
    if (shape) return { kind: 'shape', body, ...shape, id };
    const face = body.faces.find((f) => f.id === id);
    if (face) return { kind: 'face', body, ...face };
    const outfit = body.outfits.find((o) => o.id === id);
    if (outfit) return { kind: 'outfit', body, ...outfit };
    const garment = (body.garments ?? []).find((g) => g.id === id);
    if (garment) return { kind: 'garment', body, ...garment };
  }
  throw new Error(`Unknown asset ${id}`);
}

function imagePrompt(asset) {
  const keep = { base: [], shape: [manifest.keepEverythingButBody] }[asset.kind] ?? [manifest.keepEverythingElse];
  const parts = [...keep, asset.prompt, manifest.pose, manifest.style];
  const negative = [manifest.negative, asset.negative].filter(Boolean).join(', ');
  return `${parts.join(' ')} --no ${negative}`;
}

const imageType = (file) => ({ '.jpg': 'jpg', '.jpeg': 'jpg', '.webp': 'webp' })[path.extname(file).toLowerCase()] ?? 'png';

/// The image edit's input: the style reference for a base, otherwise its body's base image.
/// Designer garment pictures go along as more images, after the base.
async function imageInput(asset) {
  if (asset.kind === 'base') {
    state.referenceToken ??= await upload(path.join(here, manifest.reference));
    save();
    return { input: state.referenceToken };
  }
  const base = state[asset.body.base.id]?.image;
  if (!base?.done) throw new Error(`Generate the ${asset.body.base.id} image first`);
  if (!asset.garments?.length) return { input: base.task };
  const images = [path.join(OUT, base.files[0]), ...asset.garments.map((g) => path.resolve(here, '../../.assets-source', g))];
  const files = [];
  for (const image of images) {
    if (!fs.existsSync(image)) throw new Error(`${image} not found`);
    files.push({ type: imageType(image), file_token: await upload(image) });
  }
  return { files };
}

const STEPS = { base: ['image', 'multiview', 'model'], shape: ['image', 'multiview', 'model'],
  outfit: ['image', 'multiview', 'model'], face: ['image', 'retexture'] };

/// Outfits made in 3D on our Hunyuan pod (Tools/clothes) rather than by Tripo.
const onPod = (asset) => asset.kind === 'outfit' && (asset.generator ?? manifest.clothesGenerator) === 'hunyuan';
const pod = (...args) => execFileSync('python3', [path.join(here, '../clothes/pod.py'), ...args], { stdio: 'inherit' });

// MARK: - Garments
//
// A garment is made on its own, not cut out of a dressed character: Tripo's image edit
// draws the designer's garment on our body's front and back renders, then takes the
// body away (a ghost-mannequin picture in our exact pose), and those pictures become
// the 3D garment. fit-garments.py fits it onto the body.

/// The body's flat front and back renders (render-body.py), which the asset build makes.
function bodyRenders(body) {
  const dir = path.join(OUT, 'renders', body.gender);
  if (!fs.existsSync(path.join(dir, 'front.png'))) {
    throw new Error(`No ${body.gender} body renders yet: run the asset build (python3 Tools/clothes/pod.py build)`);
  }
  return dir;
}

/// A garment's front and back pictures for the 3D step: Tripo's redraws (front.png and back.png in its
/// folder, or in the folder of the garment it shares them with), or the designer's own with "redraw": false.
function garmentPictures(asset) {
  if (asset.redraw === false) return ['front', 'back'].map((view) => path.resolve(here, '../../.assets-source', asset.pictures[view]));
  return ['front', 'back'].map((view) => path.join(OUT, asset.sameViewsAs ?? asset.id, `${view}.png`));
}
const generatorOf = (asset) => asset.generator ?? 'tripo';

function garmentPrompt(asset, view) {
  const { [view]: prompt, style, negative } = manifest.garmentPrompt;
  return `${prompt.replaceAll('{garment}', asset.describe)} ${style} --no ${negative}`;
}

const commands = {
  async balance() {
    console.log(await call('GET', '/account/balance'));
  },

  async status() {
    for (const [id, record] of Object.entries(state)) {
      if (typeof record !== 'object') continue;
      const steps = Object.entries(record).map(([name, s]) => `${name}:${s.done ? `done(${s.credits ?? '?'})` : 'pending'}`);
      console.log(id.padEnd(24), steps.join('  '));
    }
  },

  async make(id) {
    const asset = find(id);
    console.log(await call('GET', '/account/balance'));
    if (asset.kind === 'garment') return commands.garment(id);
    if (onPod(asset)) return commands.clothes(id);
    for (const name of STEPS[asset.kind]) await commands[name](id);
  },

  /// Outfits on the Hunyuan pod: Tripo's dressed image for each, then one pod session for all
  /// of them (start, 3D models, shut down). Rerunning skips outfits that have a model.
  async clothes(...ids) {
    if (!ids.length) throw new Error('usage: node generate.mjs clothes <outfit id> [...]');
    const assets = ids.map(find);
    const wrong = assets.filter((asset) => !onPod(asset)).map((asset) => asset.id);
    if (wrong.length) throw new Error(`Not made on the Hunyuan pod: ${wrong.join(', ')}`);
    for (const asset of assets) await commands.image(asset.id);
    const todo = assets.filter((asset) => process.argv.includes('--force') || !fs.existsSync(path.join(OUT, asset.id, 'model.glb')));
    if (!todo.length) return console.log('every outfit already has a model; --force to redo');
    pod('up');
    try {
      pod('run', ...todo.flatMap((asset) => [path.join(OUT, asset.id, 'image.png'), path.join(OUT, asset.id, 'model.glb')]));
    } finally {
      pod('down');
    }
    console.log('Next: cd Tools && npm run build-assets');
  },

  /// Garments: Tripo's redraws of their pictures (unless "redraw": false), then 3D from front and
  /// back. Reruns skip what's already made.
  async garment(...ids) {
    if (!ids.length) throw new Error('usage: node generate.mjs garment <garment id> [...]');
    const assets = ids.map(find);
    const wrong = assets.filter((asset) => asset.kind !== 'garment').map((asset) => asset.id);
    if (wrong.length) throw new Error(`Not garments: ${wrong.join(', ')}`);
    for (const asset of assets.filter((a) => a.redraw !== false)) await commands.views(asset.id);
    for (const asset of assets.filter((a) => generatorOf(a) === 'tripo')) await commands.garment3d(asset.id);
    const todo = assets.filter((asset) => generatorOf(asset) === 'hunyuan'
      && (process.argv.includes('--force') || !fs.existsSync(path.join(OUT, asset.id, 'model.glb'))));
    if (todo.length) {
      pod('up');
      try {
        pod('run', ...todo.flatMap((asset) => [garmentPictures(asset)[0], path.join(OUT, asset.id, 'model.glb')]));
      } finally {
        pod('down');
      }
    }
    console.log('Next: cd Tools && npm run build-assets');
  },

  /// A garment's ghost-mannequin pictures: the body render and the designer's picture, front and back.
  async views(id) {
    const asset = find(id);
    if (asset.sameViewsAs) return commands.views(asset.sameViewsAs);
    const renders = bodyRenders(asset.body);
    for (const view of ['front', 'back']) {
      await step(id, view, async () => {
        const files = [];
        for (const image of [path.join(renders, `${view}.png`), path.resolve(here, '../../.assets-source', asset.pictures[view])]) {
          if (!fs.existsSync(image)) throw new Error(`${image} not found`);
          files.push({ type: imageType(image), file_token: await upload(image) });
        }
        return (await call('POST', '/generation/image-to-image', {
          ...manifest.settings.image, files, prompt: garmentPrompt(asset, view),
        })).task_id;
      }, async (output) => [await download(pick(output, 'generated_image_url', 'image_url'), path.join(OUT, id, `${view}.png`))]);
    }
  },

  /// Tripo's 3D garment from its front and back pictures.
  async garment3d(id) {
    const asset = find(id);
    const [front, back] = garmentPictures(asset);
    const { endpoint, ...settings } = manifest.settings.garment3d;
    await step(id, 'model', async () => {
      const inputs = [{ front: await upload(front) }, { back: await upload(back) }];
      return (await call('POST', endpoint, { ...settings, inputs })).task_id;
    }, async (output) => {
      const files = [await download(pick(output, 'pbr_model_url', 'model_url', 'model'), path.join(OUT, id, 'model.glb'))];
      const render = pick(output, 'rendered_image_url', 'rendered_image');
      if (render) files.push(await download(render, path.join(OUT, id, 'render.webp')));
      return files;
    });
  },

  async image(id) {
    const asset = find(id);
    await step(id, 'image', async () => (await call('POST', '/generation/image-to-image', {
      ...manifest.settings.image, ...await imageInput(asset), prompt: imagePrompt(asset),
    })).task_id, async (output) => [await download(pick(output, 'generated_image_url', 'image_url'), path.join(OUT, id, 'image.png'))]);
  },

  async multiview(id) {
    const image = state[id]?.image;
    if (!image?.done) throw new Error(`Generate the image for ${id} first`);
    await step(id, 'multiview', async () => (await call('POST', '/generation/image-to-multiview', { input: image.task })).task_id,
      async (output) => Promise.all(['front', 'left', 'back', 'right'].map((view) =>
        download(pick(output, `${view}_view_url`), path.join(OUT, id, `${view}.jpg`)))));
  },

  async model(id) {
    const asset = find(id);
    const views = state[id]?.multiview;
    if (!views?.done) throw new Error(`Generate the multiview for ${id} first`);
    const { endpoint, face_limit: limits, ...settings } = manifest.settings.model3d;
    await step(id, 'model', async () => (await call('POST', endpoint, {
      ...settings, inputs: [{ task_id: views.task }], face_limit: asset.kind === 'outfit' ? limits.outfit : limits.body,
    })).task_id, async (output) => {
      const files = [await download(pick(output, 'pbr_model_url', 'model_url', 'model'), path.join(OUT, id, 'model.glb'))];
      const render = pick(output, 'rendered_image_url', 'rendered_image');
      if (render) files.push(await download(render, path.join(OUT, id, 'render.webp')));
      return files;
    });
  },

  async segment(id) {
    const model = state[id]?.model;
    if (!model?.done) throw new Error(`Generate the model for ${id} first`);
    fs.mkdirSync(path.join(OUT, id), { recursive: true });
    const { endpoint, ...settings } = manifest.settings.segment;
    await step(id, 'segment', async () => (await call('POST', endpoint, { ...settings, input: model.task })).task_id,
      async (output) => {
        fs.writeFileSync(path.join(OUT, id, 'segment-output.json'), JSON.stringify(output, null, 2));
        const url = pick(output, 'model_url', 'seg_model_url', 'pbr_model_url', 'model');
        return url ? [await download(url, path.join(OUT, id, 'segmented.glb'))] : [];
      });
  },

  async retexture(id) {
    const image = state[id]?.image;
    const base = state[find(id).body.base.id]?.model;
    if (!image?.done || !base?.done) throw new Error(`Need the ${id} image and the base model first`);
    const { endpoint, ...settings } = manifest.settings.retexture;
    // The texture prompt takes a file, not a task ID.
    const token = await upload(path.join(OUT, image.files[0]));
    await step(id, 'retexture', async () => (await call('POST', endpoint, {
      ...settings, input: base.task, texture_prompt: { image: token },
    })).task_id, async (output) => [await download(pick(output, 'pbr_model_url', 'model_url', 'model'), path.join(OUT, id, 'model.glb'))]);
  },
};

const [command, ...ids] = process.argv.slice(2).filter((arg) => !arg.startsWith('--'));
if (!commands[command]) {
  console.error(`usage: node generate.mjs ${Object.keys(commands).join('|')} [id] [--force]`);
  process.exit(1);
}
try {
  await commands[command](...ids);
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
