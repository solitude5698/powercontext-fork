# 纸间 · 本地知识库 —— 一页技术说明

赛题：阶跃星辰方向「浏览器端 on-device embedding + seekdb 混合检索」
代码：`stepfun/`（`server/` seekdb-js 网关 + `web/` Vite 前端），实测数据：`stepfun/bench/`、`stepfun/logs/verification.log`

## 1. 架构

```
浏览器（Web Worker）                          Node 网关（Express, :8790）           seekdb 1.3.0 (:2882)
┌───────────────────────────────┐  文本   ┌──────────────────────────┐   SQL   ┌─────────────────────────┐
│ TextEmbeddingPipeline          │ ──────▶ │ POST /api/documents       │ ──────▶ │ c$v2$<id> 表             │
│  BGE-small-zh-v1.5 INT8 (ONNX) │  向量   │ POST /api/search          │         │  VECTOR(512) HNSW(cos)   │
│  WebGPU 优先 → WASM 降级        │ ◀────── │  hybridSearch(RRF 库内融合)│ ◀────── │  FULLTEXT(ik, smart)     │
└───────────────────────────────┘         └──────────────────────────┘         └─────────────────────────┘
```

- 文档与查询的向量化全部在浏览器内完成，无任何云端推理调用；模型文件随仓库分发，`env.allowRemoteModels = false`。
- seekdb 既存原文与向量，也负责两路召回与融合；客户端只做「文本→向量」和结果渲染，不参与排名合并。

## 2. 模型与量化

| 项 | 值 |
| --- | --- |
| 模型 | `Xenova/bge-small-zh-v1.5`（BAAI bge-small-zh-v1.5 的 ONNX 转换版，MIT） |
| 结构 | BertModel，4 层 / 8 头 / hidden 512 / 词表 21128，max 512 tokens |
| 量化 | 动态 INT8（ONNX Runtime MatMulInteger，`onnx/model_quantized.onnx`） |
| 体积 | FP32 94,851,877 B → INT8 24,010,842 B，**压缩 3.95×** |
| 获取 | `node scripts/fetch-model.mjs`（INT8）/ `--fp32`（基线），源 hf-mirror.com，按 content-length 校验 |
| 查询侧 | 加 BGE 指令前缀「为这个句子生成表示以用于检索相关文章：」，文档侧不加 |
| 池化 | mean pooling + L2 normalize，余弦距离 |

质量变化（24 条语料、12 条查询，gold = 话题标签，Node CPU 复算，`bench/quality-results.json`）：

| 指标 | FP32 | INT8 | 变化 |
| --- | --- | --- | --- |
| Recall@1 | 83.3% | 83.3% | 0 |
| Recall@3 | 100% | 100% | 0 |
| MRR | 0.903 | 0.903 | 0 |
| 向量余弦一致性 | — | 0.9909（与 FP32 逐条对比的均值） | -0.009 |
| 64 条文本向量化 | 167 ms | 77 ms | 2.2× 更快 |

排序结果逐条完全一致：量化只带来极小的向量扰动，端侧检索质量无损。

## 3. 端侧推理：WebGPU 优先 + WASM 兜底

`web/src/worker.js`：

1. `init(preferred)`：`'gpu' in navigator` 时先尝试 `pipeline(..., {device:'webgpu', dtype:'q8'})`；抛错则 `postMessage({type:'status', phase:'fallback'})`，状态栏显示「WebGPU 不可用，正在降级到 WASM…」，再以 `{device:'wasm', dtype:'q8'}` 加载。
2. 加载完成后执行 8 条预热推理（真实笔记），把首轮 kernel/shader 编译与内存分配计入 `loadMs` 再上报 `ready`，界面徽标显示后端（WebGPU/WASM）与精度（INT8/FP16…）。
3. `embed(id, texts, batch, kind)`：`kind==='query'` 时统一加指令前缀；按 `batchSize` 分批推理，每批回传进度；结果与耗时一并回传，UI 状态栏显示「向量化 X ms · 检索 Y ms · 端到端 Z ms」。
4. 模型与 ORT 运行时全部本地：`env.localModelPath='/models/'`、`useBrowserCache=true`、`wasmPaths='/ort/'`；ORT 的 wasm/mjs 在 `npm install` 时由 `scripts/sync-ort.mjs` 从 `onnxruntime-web` 复制。

URL 参数：`?device=webgpu|wasm` 强制后端（`wasm` 用于验证降级路径），`?batch=N` 调整批大小。

## 4. seekdb 存储与库内混合检索

集合由 seekdb-js 创建，落表结构（`SHOW CREATE TABLE` 实测）：

```sql
CREATE TABLE `c$v2$<id>` (
  `_id` varbinary(512) NOT NULL,
  `document` string, `embedding` VECTOR(512), `metadata` json,
  PRIMARY KEY (`_id`),
  FULLTEXT KEY `idx_fts` (`document`) WITH PARSER ik PARSER_PROPERTIES=(ik_mode="smart"),
  VECTOR KEY `idx_vec` (`embedding`) WITH (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG,
    M=16, EF_CONSTRUCTION=200, EF_SEARCH=64, SYNC_MODE=ASYNC)
);
```

检索（`server/src/index.mjs`）：`collection.hybridSearch({ query:{ whereDocument:{ $contains: q } }, knn:{ queryEmbeddings:[v] }, rank:{ rrf:{ rankWindowSize:40, rankConstant:60 } }, nResults })` → 库内 `DBMS_HYBRID_SEARCH`，全文和向量两路召回与 RRF 融合都在 seekdb 执行；仅当全文无命中时回退纯向量查询，并在 UI 标注「向量回退」。

增量：`add()` 按 64 条一批写入，写入返回即可检索（HNSW 与倒排索引在库内刷新），实测 40→43 条追加后新笔记直接排到命中第 1。集合列表/切换/清空/删除均通过网关 API 完成，元数据存于 `sdk_collections`。

## 5. 性能实测（Chromium 150，AMD Radeon 780M，INT8，batch=8）

| 阶段 | WebGPU | WASM |
| --- | --- | --- |
| 模型加载（含预热） | 1650 / 2031 ms | 989 / 995 / 1029 ms |
| 首次导入 8 条（含首轮编译） | 2048 / 2436 ms | 1312 / 1344 ms |
| 热态导入 40 条 | 1703 ms | 1328 ms（batch=1: 1318 ms） |
| 增量导入 3 条（热态） | 161 ms | — |
| 查询向量化 | 135 / 98 ms | 24 / 25 ms |
| seekdb 混合检索 | 20.2 / 8.0 ms | 17.5 / 7.2 ms |
| 端到端查询 | 185 / 125 ms | 62 / 62 ms |

（多个数字为该配置下的重复实测值；单值项为同会话代表值。）

结论与优化：

- 首查比热查慢 2–3 倍，主要来自 WebGPU shader 编译与 ORT 图初始化——用预热推理把这段开销挪到「加载」阶段，用户在首次检索时拿到的是热态延迟。
- **小模型 + 集显上 WASM 显著快于 WebGPU**（查询向量化 24 ms vs 98 ms）：BGE-small INT8 计算量小，WebGPU 每次 dispatch/同步的固定开销压倒了算力收益。应用仍按题目要求优先 WebGPU 并显式展示后端，用户可用 `?device=wasm` 在低算力设备上获得更低延迟。
- 批大小对短文本几乎无影响（40 条 batch=8 1328 ms vs batch=1 1318 ms），单条约 32 ms 已接近线性下界；保留批处理是为长文本场景。
- seekdb 检索稳定在 7–21 ms，与集合规模（8–43 条）基本无关，瓶颈始终在端侧向量化而非数据库。
- 相关成本优化：INT8 模型 24 MB 可随站点分发；模型走浏览器 Cache Storage 与 HTTP 缓存；ORT 运行时本地化，无 CDN 往返。

## 6. 复现

```bash
# seekdb（本地 server 或 docker，端口 2882）→ 网关 → 前端
cd stepfun/server && npm install && cp .env.example .env && npm start   # :8790
cd ../web && npm install && npm run dev                                # :5173
node scripts/fetch-model.mjs --fp32 && node scripts/quality-eval.mjs C:/tmp/bge-fp32-model
```

完整操作路径、降级验证、增量验证与排查记录见 `stepfun/logs/verification.log`；原始计时与质量数据见 `stepfun/bench/*.json`。

## 7. 已知限制

- 仅文本；图片/PDF 解析未接入。
- 网关无鉴权，仅用于本地演示；`web/public/models` 内置模型使仓库体积约 24 MB。
- 质量评估语料较小（24 条 / 12 查询），结论用于量化前后对比而非绝对水平。
