import { env, pipeline } from '@huggingface/transformers';

env.allowLocalModels = true;
env.localModelPath = '/models/';
env.allowRemoteModels = false;
env.useBrowserCache = true;
env.backends.onnx.wasm.wasmPaths = '/ort/';

const MODEL_ID = 'Xenova/bge-small-zh-v1.5';
const QUERY_INSTRUCTION = '为这个句子生成表示以用于检索相关文章：';
const WARMUP_TEXTS = [
  'HNSW 用分层可导航小世界图组织向量，近邻检索复杂度接近对数级。',
  'WebGPU 允许浏览器直接调度 GPU 执行 ONNX 计算。',
  'INT8 量化把权重压缩到 8 位整数，模型体积约为四分之一。',
  'seekdb 同时提供向量索引和全文索引，可在库内完成混合检索。',
  'BM25 依据词频和逆文档频率打分，擅长精确关键词匹配。',
  'RRF 把两路检索的名次转换为分数相加，无需对齐量纲。',
  '写周报时先列本周完成的三件事，再补风险和下周计划。',
  '考试前复习错题优先看错因归纳，而不是重做已会的题。',
];
let extractor = null;
let runtime = { device: null, dtype: null };

const report = (payload) => self.postMessage(payload);

async function loadPipeline(device, dtype) {
  extractor = await pipeline('feature-extraction', MODEL_ID, {
    device,
    dtype,
    progress_callback: (event) => report({ type: 'progress', event }),
  });
  // First inference pays shader compilation / kernel selection. Warm up with a
  // realistic batch and read the output back, which forces the GPU queue to
  // finish, so the user's first import runs at steady-state speed.
  const warmup = await extractor(WARMUP_TEXTS, { pooling: 'mean', normalize: true });
  warmup.tolist();
  runtime = { device, dtype };
}

async function init(preferredDevice) {
  if (extractor) {
    report({ type: 'ready', ...runtime });
    return;
  }
  const started = performance.now();
  report({ type: 'status', phase: 'loading', device: preferredDevice });
  if (preferredDevice === 'webgpu') {
    try {
      await loadPipeline('webgpu', 'q8');
      report({ type: 'ready', ...runtime, loadMs: performance.now() - started });
      return;
    } catch (error) {
      report({ type: 'status', phase: 'fallback', detail: String(error?.message ?? error) });
    }
  }
  await loadPipeline('wasm', 'q8');
  report({ type: 'ready', ...runtime, loadMs: performance.now() - started });
}

async function embed(id, texts, batchSize, kind) {
  const started = performance.now();
  const input = kind === 'query' ? texts.map((text) => QUERY_INSTRUCTION + text) : texts;
  const embeddings = [];
  for (let start = 0; start < input.length; start += batchSize) {
    const slice = input.slice(start, start + batchSize);
    const output = await extractor(slice, { pooling: 'mean', normalize: true });
    embeddings.push(...output.tolist());
    report({
      type: 'batch',
      id,
      done: Math.min(start + batchSize, input.length),
      total: input.length,
      ms: performance.now() - started,
    });
  }
  report({ type: 'result', id, embeddings, ms: performance.now() - started, ...runtime });
}

self.onmessage = async (event) => {
  const message = event.data;
  try {
    if (message.type === 'init') {
      await init(message.device);
    } else if (message.type === 'embed') {
      await embed(message.id, message.texts, message.batchSize ?? 8, message.kind);
    }
  } catch (error) {
    report({ type: 'error', id: message.id, message: String(error?.message ?? error) });
  }
};
