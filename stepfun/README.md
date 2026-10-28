# 纸间 · 本地知识库（seekdb × 阶跃星辰赛题）

浏览器本地完成 embedding，向量与原文写入 seekdb，检索由 seekdb 在库内完成向量 + 全文混合检索（RRF）。无云端推理。

- 前端：Vite + [Transformers.js](https://github.com/huggingface/transformers.js) 4.3.1（ONNX Runtime Web 1.31），Web Worker 内推理
- 模型：`Xenova/bge-small-zh-v1.5`（MIT，中文 BGE-small 的 ONNX 版本），INT8 动态量化，512 维，查询侧加 BGE 检索指令前缀
- 数据层：seekdb 1.3.0（seekdb-js 1.4.0），HNSW(cosine) + FULLTEXT(ik) + `DBMS_HYBRID_SEARCH`

## 目录

```
stepfun/
├── README.md                  # 本文件：启动与复现
├── TECHNICAL_NOTE.md          # 一页技术说明（模型/量化/端侧/seekdb/性能）
├── server/                    # seekdb-js HTTP 网关（Express）
│   ├── src/index.mjs          # 集合管理、写入、混合检索
│   └── .env.example
├── web/                       # 浏览器端知识库
│   ├── src/worker.js          # 模型加载、WebGPU→WASM、批处理推理
│   ├── src/main.js            # UI 与网关调用
│   ├── public/models/         # 内置 INT8 模型（24.0 MB）
│   ├── scripts/sync-ort.mjs   # 安装时从 onnxruntime-web 同步 wasm 运行时
│   ├── scripts/fetch-model.mjs# 重新下载 INT8 / FP32 基线模型
│   └── scripts/quality-eval.mjs# 量化前后 Recall@K / MRR 对比
├── bench/                     # 实测数据（quality-results.json / timings.json）
└── logs/verification.log      # 端到端验证记录
```

## 启动

前置：Node.js ≥ 20；可访问的 seekdb 实例（题目允许本地 server 部署）。

```bash
# 1. seekdb（任选其一）
docker run -d --name seekdb -p 2882:2882 oceanbase/seekdb:latest
# 或使用已有的 seekdb server，确认端口与账号

# 2. 网关
cd stepfun/server
npm install
cp .env.example .env        # 按实际实例修改 SEEKDB_HOST/PORT/PASSWORD
npm start                   # http://127.0.0.1:8790

# 3. Web
cd ../web
npm install                 # postinstall 会把 ORT wasm 运行时同步到 public/ort
npm run dev                 # http://localhost:5173
# 生产构建：npm run build && npm run preview
```

`web/public/models` 已内置 INT8 模型，无需联网即可推理；如需重新获取或下载 FP32 基线：

```bash
node scripts/fetch-model.mjs          # INT8（写入 public/models）
node scripts/fetch-model.mjs --fp32   # FP32 基线（写入 FP32_ROOT，默认 C:/tmp/bge-fp32-model）
```

## 使用

1. 打开页面，顶部徽标显示当前推理后端（`WebGPU` / `WASM`）与精度（`INT8`）。
2. 左栏选择/新建知识库，粘贴或「载入示例」，点击「向量化并写入」。进度条分别展示模型加载与向量化进度。
3. 右栏输入问题，点击「检索」。结果区展示原文、相似度与元数据；状态栏给出向量化、seekdb 检索与端到端耗时，并标明检索模式（混合 / 向量 / 向量回退）。

测试参数（URL query）：

| 参数 | 作用 |
| --- | --- |
| `?device=webgpu` | 强制 WebGPU（失败仍会自动降级 WASM） |
| `?device=wasm` | 模拟不支持 WebGPU 的环境，验证降级路径 |
| `?batch=1` | 调整 embedding 批大小（默认 8），用于批量对比 |

知识库的增量写入、切换与清空都在左栏完成；写入完成后立即可检索（见 `logs/verification.log`）。

## seekdb 数据与检索

网关通过 seekdb-js 创建集合，实际落表结构（`SHOW CREATE TABLE` 实测）：

```sql
CREATE TABLE `c$v2$<collection_id>` (
  `_id` varbinary(512) NOT NULL,
  `document` string,
  `embedding` VECTOR(512),
  `metadata` json,
  PRIMARY KEY (`_id`),
  FULLTEXT KEY `idx_fts` (`document`) WITH PARSER ik PARSER_PROPERTIES=(ik_mode="smart"),
  VECTOR KEY `idx_vec` (`embedding`) WITH (DISTANCE=COSINE, TYPE=HNSW, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64)
);
```

检索接口调用集合的 `hybridSearch`（库内 `DBMS_HYBRID_SEARCH`）：

- whereDocument `$contains` 走全文召回，`knn` 走向量召回，`rank.rrf` 在库内融合（rankWindowSize 40 / rankConstant 60）
- 查询为空或全文无命中时回退为纯向量检索；两路结果不会在客户端拼合

## 复现测试

```bash
# 网关接口冒烟
curl http://127.0.0.1:8790/api/health
curl http://127.0.0.1:8790/api/collections

# 量化前后检索质量（需先下载 FP32 基线）
cd web
node scripts/fetch-model.mjs --fp32
node scripts/quality-eval.mjs "C:/tmp/bge-fp32-model"
```

实测数据见 `bench/quality-results.json` 与 `bench/timings.json`；测试条件、完整流程与截图中的数字均记录在 `TECHNICAL_NOTE.md`。

## 已知限制

- 仅支持文本；图片 OCR 未实现。
- 本机（AMD Radeon 780M 集显 + Chromium 150）上 INT8 模型的 WebGPU 与 WASM 推理速度接近，WebGPU 首次 batch 有额外编译开销；页面按题目要求优先尝试 WebGPU，失败或不可用时自动降级并在界面标注。
- 网关未实现鉴权，仅用于本地演示；请勿直接暴露到公网。
