import { copyFileSync, mkdirSync, readdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const appRequire = createRequire(join(here, '..', 'package.json'));

function resolveOrtDist() {
  try {
    return dirname(appRequire.resolve('onnxruntime-web'));
  } catch {
    const transformersEntry = appRequire.resolve('@huggingface/transformers');
    return dirname(createRequire(transformersEntry).resolve('onnxruntime-web'));
  }
}

const source = resolveOrtDist();
const target = join(here, '..', 'public', 'ort');

mkdirSync(target, { recursive: true });
const files = readdirSync(source).filter((name) => /^ort-wasm-simd-threaded.*\.(mjs|wasm)$/.test(name));
for (const name of files) copyFileSync(join(source, name), join(target, name));
console.log(`[ort] ${files.length} runtime files synced from ${source} -> public/ort`);
