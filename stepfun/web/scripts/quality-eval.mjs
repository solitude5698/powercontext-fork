// Compare retrieval quality of the INT8 model against the FP32 baseline.
// Usage: node scripts/quality-eval.mjs [fp32ModelRoot] [outFile]
// fp32ModelRoot must mirror localModelPath layout:
//   <root>/Xenova/all-MiniLM-L6-v2/{config.json,tokenizer.json,...,onnx/model.onnx}
// The INT8 model is read from this repo's public/models directory.

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { env, pipeline } from '@huggingface/transformers';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, '..');
const fp32Root = process.argv[2] ?? 'C:/tmp/bge-fp32-model';
const outFile = process.argv[3] ?? join(webRoot, '..', 'bench', 'quality-results.json');
const MODEL_ID = 'Xenova/bge-small-zh-v1.5';
const QUERY_INSTRUCTION = '为这个句子生成表示以用于检索相关文章：';

const corpus = [
  { id: 'hnsw', topic: 'hnsw', text: 'HNSW 用分层可导航小世界图组织向量，查询时从稀疏层逐层下沉，把近邻检索的复杂度降到对数级。' },
  { id: 'hnsw2', topic: 'hnsw', text: '分层图索引让向量检索不必扫描全量数据，顶部稀疏层负责快速跳转。' },
  { id: 'webgpu', topic: 'webgpu', text: 'WebGPU 允许浏览器直接调度 GPU，Transformers.js 在支持 WebGPU 的环境下会优先把 ONNX 计算放到显卡上执行。' },
  { id: 'webgpu2', topic: 'webgpu', text: '浏览器通过 WebGPU 接口访问显卡算力，适合运行端侧推理模型。' },
  { id: 'int8', topic: 'int8', text: 'INT8 量化把权重从 32 位浮点压缩到 8 位整数，模型体积约缩小到四分之一，端侧加载更快。' },
  { id: 'int8b', topic: 'int8', text: '把模型权重量化为 8 位整数可以显著减小体积，适合在本地设备加载。' },
  { id: 'seekdb', topic: 'seekdb', text: 'seekdb 兼容 MySQL 协议，同时提供向量索引和全文索引，可以在一次查询里完成混合检索。' },
  { id: 'seekdb2', topic: 'seekdb', text: '数据库内置向量列与全文索引，应用不必再拼接两路检索结果。' },
  { id: 'bm25', topic: 'bm25', text: 'BM25 依靠词频和逆文档频率打分，擅长精确关键词；向量检索擅长语义相近但用词不同的情况。' },
  { id: 'bm252', topic: 'bm25', text: '关键词打分依赖词频统计，适合查找专有名词和精确短语。' },
  { id: 'rrf', topic: 'rrf', text: 'RRF（倒数排名融合）把两路检索的名次转换为分数相加，无需对齐两路分值的量纲。' },
  { id: 'rrf2', topic: 'rrf', text: '按排名倒数加权融合，可以让向量召回和关键词召回共同决定最终顺序。' },
  { id: 'weekly', topic: 'weekly', text: '写周报时先列本周完成的三件事，再补风险和下周计划，最后压到一页以内。' },
  { id: 'weekly2', topic: 'weekly', text: '周报结构建议：成果、风险、下一步计划，控制在一页。' },
  { id: 'exams', topic: 'exams', text: '考试前复习错题优先看错因归纳，而不是重新做一遍已经会的题。' },
  { id: 'exams2', topic: 'exams', text: '错题本的价值在错因分类，重复做会做的题收益很低。' },
  { id: 'quantum', topic: 'quantum', text: 'Shor 算法可以在多项式时间内分解大整数，对 RSA 加密构成威胁。' },
  { id: 'quantum2', topic: 'quantum', text: '量子计算中的 Shor 算法利用周期查找分解整数。' },
  { id: 'cache', topic: 'cache', text: '浏览器 Cache Storage 可以缓存模型文件，第二次加载无需重新下载。' },
  { id: 'cache2', topic: 'cache', text: '把 ONNX 模型缓存到本地后，刷新页面可以快速完成初始化。' },
  { id: 'tokenize', topic: 'tokenize', text: '分词器把文本切成 token 并补齐到固定长度，批量推理需要按长度分桶减少填充。' },
  { id: 'tokenize2', topic: 'tokenize', text: '输入文本先经过 tokenizer 编码，再送入 ONNX 模型得到向量。' },
  { id: 'payload', topic: 'payload', text: '知识库的向量、原文和元数据一起写入数据库，检索时直接返回原文。' },
  { id: 'payload2', topic: 'payload', text: '每条记录保存文档 ID、正文与元信息，方便增量更新与删除。' },
];

const queries = [
  { text: '向量检索为什么要用分层图', topic: 'hnsw' },
  { text: '浏览器怎么直接使用显卡算力', topic: 'webgpu' },
  { text: '8 位量化能把模型压缩多少', topic: 'int8' },
  { text: '哪种数据库能同时做语义和关键词检索', topic: 'seekdb' },
  { text: '精确关键词打分用的是什么算法', topic: 'bm25' },
  { text: '倒排排名融合是什么意思', topic: 'rrf' },
  { text: '周报应该怎么写结构', topic: 'weekly' },
  { text: '考试前怎么复习错题', topic: 'exams' },
  { text: '什么算法能快速分解大整数', topic: 'quantum' },
  { text: '模型文件第二次打开还要下载吗', topic: 'cache' },
  { text: '批量推理为什么要按长度分桶', topic: 'tokenize' },
  { text: '每条知识记录应该存哪些字段', topic: 'payload' },
];

function cosine(a, b) {
  let sum = 0;
  for (let i = 0; i < a.length; i += 1) sum += a[i] * b[i];
  return sum;
}

async function embedAll(modelRoot, dtype) {
  env.allowLocalModels = true;
  env.allowRemoteModels = false;
  env.localModelPath = modelRoot.endsWith('/') ? modelRoot : `${modelRoot}/`;
  const extractor = await pipeline('feature-extraction', MODEL_ID, { device: 'cpu', dtype });
  const texts = [
    ...corpus.map((item) => item.text),
    ...queries.map((item) => QUERY_INSTRUCTION + item.text),
  ];
  const started = performance.now();
  const output = await extractor(texts, { pooling: 'mean', normalize: true });
  const ms = performance.now() - started;
  const vectors = output.tolist();
  return {
    corpusVectors: vectors.slice(0, corpus.length),
    queryVectors: vectors.slice(corpus.length),
    ms,
  };
}

function evaluate({ corpusVectors, queryVectors }) {
  let hit1 = 0;
  let hit3 = 0;
  let mrr = 0;
  const rows = [];
  queries.forEach((query, index) => {
    const goldIndexes = corpus.map((item, i) => (item.topic === query.topic ? i : -1)).filter((i) => i >= 0);
    const scores = corpusVectors.map((vector) => cosine(queryVectors[index], vector));
    const order = scores
      .map((score, corpusIndex) => ({ score, corpusIndex }))
      .sort((a, b) => b.score - a.score);
    const rank = order.findIndex((item) => goldIndexes.includes(item.corpusIndex)) + 1;
    if (rank === 1) hit1 += 1;
    if (rank <= 3) hit3 += 1;
    mrr += 1 / rank;
    rows.push({ query: query.text, topic: query.topic, rank, top: corpus[order[0].corpusIndex].id });
  });
  return {
    recallAt1: hit1 / queries.length,
    recallAt3: hit3 / queries.length,
    mrr: mrr / queries.length,
    rows,
  };
}

function vectorDrift(a, b) {
  let sum = 0;
  for (let i = 0; i < a.corpusVectors.length; i += 1) {
    sum += cosine(a.corpusVectors[i], b.corpusVectors[i]);
  }
  return sum / a.corpusVectors.length;
}

const fp32 = await embedAll(fp32Root, 'fp32');
const int8 = await embedAll(join(webRoot, 'public', 'models'), 'q8');
const fp32Score = evaluate(fp32);
const int8Score = evaluate(int8);

const results = {
  model: MODEL_ID,
  runtime: `transformers.js (Node, CPU)`,
  corpusSize: corpus.length,
  queryCount: queries.length,
  fp32: {
    recallAt1: fp32Score.recallAt1,
    recallAt3: fp32Score.recallAt3,
    mrr: fp32Score.mrr,
    embedMs64: Math.round(fp32.ms),
  },
  int8: {
    recallAt1: int8Score.recallAt1,
    recallAt3: int8Score.recallAt3,
    mrr: int8Score.mrr,
    embedMs64: Math.round(int8.ms),
  },
  meanCosineFp32VsInt8: vectorDrift(fp32, int8),
  perQuery: fp32Score.rows.map((row, index) => ({
    ...row,
    int8Rank: int8Score.rows[index].rank,
    int8Top: int8Score.rows[index].top,
  })),
};

mkdirSync(dirname(outFile), { recursive: true });
writeFileSync(outFile, `${JSON.stringify(results, null, 2)}\n`);

console.log(`FP32  Recall@1 ${(fp32Score.recallAt1 * 100).toFixed(1)}%  Recall@3 ${(fp32Score.recallAt3 * 100).toFixed(1)}%  MRR ${fp32Score.mrr.toFixed(3)}`);
console.log(`INT8  Recall@1 ${(int8Score.recallAt1 * 100).toFixed(1)}%  Recall@3 ${(int8Score.recallAt3 * 100).toFixed(1)}%  MRR ${int8Score.mrr.toFixed(3)}`);
console.log(`mean cosine(fp32, int8) over corpus: ${vectorDrift(fp32, int8).toFixed(4)}`);
console.log(`wrote ${outFile}`);
