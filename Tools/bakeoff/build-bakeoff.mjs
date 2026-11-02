// Builds the Demo app's generator comparison: outfit A as Pixal3D and Hunyuan3D made it,
// cut into items for the same base body as the Tripo items.
//
//   node bakeoff/build-bakeoff.mjs        (from Tools/, after build-assets.mjs)
//
// Writes Demo/AvatarDemo/Bakeoff/: base.glb (the base body with patches for every item,
// Tripo's and these), Items/*.glb and bakeoff.json. Demo only: Pixal3D's items are cut at
// full resolution and are large.

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { dedup, prune } from '@gltf-transform/functions';

const here = path.dirname(fileURLToPath(import.meta.url));
const tools = path.dirname(here);
const SOURCE = path.resolve(tools, '../.assets-source');
const OUT = path.resolve(tools, '../Demo/AvatarDemo/Bakeoff');
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS);
const manifest = JSON.parse(fs.readFileSync(path.join(here, 'outfits.json'), 'utf8'));
const tripo = JSON.parse(fs.readFileSync(path.join(tools, 'tripo/assets.json'), 'utf8'));
const tripoBody = tripo.bodies.find((b) => b.gender === manifest.body.gender);
const generated = path.join(SOURCE, 'Tripo/gen');

// Bake-off textures are atlases of many small islands, so they keep more resolution than the catalog's.
const BASE_COLOR_SIZE = 2048;
const NORMAL_SIZE = 512;

/// Other generators' models with WebP textures saved as PNG, which the Python tools read.
/// Full resolution on purpose: simplifying an atlas of small islands smears colors at
/// island edges, and the cutter reads the colors.
async function prepare(model) {
  const prepared = model.replace(/\.glb$/, '.prepared.glb');
  const doc = await io.read(model);
  for (const texture of doc.getRoot().listTextures()) {
    if (texture.getMimeType() !== 'image/webp') continue;
    const png = await sharp(Buffer.from(texture.getImage())).png().toBuffer();
    texture.setImage(new Uint8Array(png)).setMimeType('image/png');
  }
  doc.getRoot().listExtensionsUsed().find((e) => e.extensionName === 'EXT_texture_webp')?.dispose();
  await io.write(prepared, doc);
  return prepared;
}

/// Base color and normal textures as JPEG; other maps dropped, as build-assets.mjs does.
async function shrinkAndWrite(from, to) {
  const doc = await io.read(from);
  for (const material of doc.getRoot().listMaterials()) {
    material.setMetallicRoughnessTexture(null).setOcclusionTexture(null).setEmissiveTexture(null);
  }
  for (const texture of doc.getRoot().listTextures()) {
    const users = texture.listParents().filter((p) => p.propertyType === 'Material');
    const isBaseColor = users.some((m) => m.getBaseColorTexture() === texture);
    const isNormal = users.some((m) => m.getNormalTexture() === texture);
    if (!isBaseColor && !isNormal) continue;
    const size = isBaseColor ? BASE_COLOR_SIZE : NORMAL_SIZE;
    const image = await sharp(Buffer.from(texture.getImage())).removeAlpha()
      .resize(size, size, { fit: 'inside', withoutEnlargement: true })
      .jpeg({ quality: isBaseColor ? 85 : 92 }).toBuffer();
    texture.setImage(new Uint8Array(image)).setMimeType('image/jpeg');
  }
  await doc.transform(prune(), dedup());
  fs.mkdirSync(path.dirname(to), { recursive: true });
  await io.write(to, doc);
  console.log(`${path.relative(OUT, to).padEnd(44)} ${(fs.statSync(to).size / 1e6).toFixed(1).padStart(6)} MB`);
}

const rigged = path.join(generated, tripoBody.base.id, 'model.rigged.glb');
if (!fs.existsSync(rigged)) {
  throw new Error(`${rigged} not found: run build-assets.mjs first`);
}

// Tripo's outfits go in too, so the base gets patches for its items as well; they're cut as build-assets cuts them.
const outfits = tripoBody.outfits.filter((outfit) => (outfit.generator ?? tripo.clothesGenerator) === 'tripo').map((outfit) => {
  const model = path.join(generated, outfit.id, 'model.simplified.glb');
  if (!fs.existsSync(model)) throw new Error(`${model} not found: run build-assets.mjs first`);
  return { model, forward: tripo.forward, items: outfit.items };
});
const outfitItems = tripoBody.outfits.find((o) => o.id === manifest.body.outfit).items;
const generators = [];
for (const generator of manifest.generators) {
  const model = path.join(SOURCE, generator.model);
  if (!fs.existsSync(model)) {
    console.warn(`skip ${generator.id}: ${generator.model} not found`);
    continue;
  }
  const items = outfitItems.map((item) => ({
    ...item, id: `${item.id}--${generator.id}`, name: `${item.name} · ${generator.short}`,
  }));
  outfits.push({ model: await prepare(model), forward: generator.forward, otherGenerator: true, items });
  generators.push({ ...generator, items });
}

const fitted = path.join(SOURCE, 'bakeoff/fitted');
fs.rmSync(fitted, { recursive: true, force: true });
fs.mkdirSync(fitted, { recursive: true });
fs.writeFileSync(path.join(fitted, 'outfits.json'), JSON.stringify(outfits, null, 2));
execFileSync('python3', [path.join(tools, 'fit-other-generators.py'), rigged, path.join(fitted, 'outfits.json'), fitted], { stdio: 'inherit' });
// The same body types as the catalog's body (build-assets.mjs), so these items follow them too.
const shapes = tripo.bodyShapes.map((shape) => [shape.id, path.join(generated, `${tripoBody.base.id}_${shape.id}`, 'model.glb')])
  .filter(([, model]) => fs.existsSync(model));
if (shapes.length) {
  execFileSync('python3', [path.join(tools, 'add-body-shapes.py'), rigged, fitted,
    ...shapes.map(([id, model]) => `${id}=${model}`), '--forward', tripo.forward], { stdio: 'inherit' });
}

fs.rmSync(OUT, { recursive: true, force: true });
await shrinkAndWrite(path.join(fitted, 'base.glb'), path.join(OUT, 'base.glb'));
const catalogItems = [];
for (const item of generators.flatMap((g) => g.items)) {
  const file = `Items/${item.id}.glb`;
  await shrinkAndWrite(path.join(fitted, `${item.id}.glb`), path.join(OUT, file));
  catalogItems.push({
    id: item.id, name: item.name, slot: item.slot, file, genders: [manifest.body.gender],
    tintMaterials: [], skinMaterials: [], hides: [], hidesSlots: [],
  });
}
const bySlot = (items) => Object.fromEntries(items.map((item) => [item.slot, item.id]));
fs.writeFileSync(path.join(OUT, 'bakeoff.json'), JSON.stringify({
  body: manifest.body.gender,
  base: 'base.glb',
  generators: [
    { id: manifest.tripo.id, name: manifest.tripo.name, items: bySlot(outfitItems) },
    ...generators.map((g) => ({ id: g.id, name: g.name, items: bySlot(g.items) })),
  ],
  items: catalogItems,
}, null, 2));
console.log(`bakeoff.json: ${generators.length} generators, ${catalogItems.length} items`);
