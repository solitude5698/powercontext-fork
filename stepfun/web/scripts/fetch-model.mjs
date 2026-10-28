// Download the model artifacts into public/models. The INT8 model is already
// vendored in the repo; this script exists for reproducibility and for
// fetching the FP32 baseline used by scripts/quality-eval.mjs.
// Usage: node scripts/fetch-model.mjs [--fp32]

import { createWriteStream, existsSync, mkdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, '..');
const mirror = process.env.HF_MIRROR ?? 'https://hf-mirror.com';
const repoPath = 'Xenova/bge-small-zh-v1.5';
const wantFp32 = process.argv.includes('--fp32');

const files = [
  'config.json',
  'tokenizer.json',
  'tokenizer_config.json',
  'special_tokens_map.json',
  'onnx/model_quantized.onnx',
];
if (wantFp32) files.push('onnx/model.onnx');

const targetRoot = wantFp32
  ? join(process.env.FP32_ROOT ?? 'C:/tmp/bge-fp32-model', repoPath)
  : join(webRoot, 'public', 'models', repoPath);

const expected = {
  'onnx/model_quantized.onnx': 24_010_842,
  'onnx/model.onnx': 94_851_877,
};

for (const file of files) {
  const target = join(targetRoot, file);
  mkdirSync(dirname(target), { recursive: true });
  const size = existsSync(target) ? statSync(target).size : 0;
  if (size > 0 && (!expected[file] || size === expected[file])) {
    console.log(`[model] skip ${file} (${size} bytes)`);
    continue;
  }
  const url = `${mirror}/${repoPath}/resolve/main/${file}`;
  console.log(`[model] downloading ${url}`);
  const response = await fetch(url);
  if (!response.ok || !response.body) throw new Error(`${response.status} ${response.statusText}`);
  await pipeline(Readable.fromWeb(response.body), createWriteStream(target));
  console.log(`[model] saved ${file} (${statSync(target).size} bytes)`);
}
console.log(`[model] done -> ${targetRoot}`);
