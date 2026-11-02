// Reduces a heavy generated mesh (Tripo, Meshy, ...) to a phone-friendly
// triangle count, keeping UVs and textures.
//
//   node simplify-mesh.mjs <in.glb> <out.glb> [targetTriangles=30000]

import { NodeIO } from '@gltf-transform/core';
import { simplify, weld, prune, dedup } from '@gltf-transform/functions';
import { MeshoptSimplifier } from 'meshoptimizer';

const [input, output, target = '30000'] = process.argv.slice(2);
if (!input || !output) {
  console.error('usage: node simplify-mesh.mjs <in.glb> <out.glb> [targetTriangles]');
  process.exit(1);
}

const io = new NodeIO();
const doc = await io.read(input);
const triangles = () => doc.getRoot().listMeshes()
  .flatMap((mesh) => mesh.listPrimitives())
  .reduce((sum, primitive) => sum + (primitive.getIndices()?.getCount() ?? 0) / 3, 0);

const before = triangles();
await MeshoptSimplifier.ready;
const ratio = Number(target) / before;
await doc.transform(
  weld(),
  ...(ratio < 1 ? [simplify({ simplifier: MeshoptSimplifier, ratio, error: 0.01 })] : []),
  prune(),
  dedup(),
);
await io.write(output, doc);
console.log(`${before} -> ${triangles()} triangles`);
