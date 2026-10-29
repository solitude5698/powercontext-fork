# 赛博客栈·老周的记性（Dify × seekdb）

一个「会记仇」的互动叙事应用：你是谁、你干了什么、欠没欠账，掌柜老周都记在 seekdb 里。
关掉对话、换个会话、过一天再来，他仍然会先提你上次砸的那只酒坛。

## 1. 玩法与分工

玩家每次输入一句「你要做的事」，例如「我把后院的酒坛砸了」「我想用一壶桂花酿抵了旧账」。
Dify Chatflow 负责整条流程：读取记忆 → 检索传说 → 记账结算 → 用大模型生成老周的回应。

| 组成 | 作用 |
| --- | --- |
| Dify（Chatflow） | 输入处理、三次 HTTP 调用、条件固定的编排、LLM 生成与输出 |
| seekdb | 玩家表 `players` 存金币/声望/互动次数；事件表 `events` 存每条行为与判定；传说表 `lore` 用 `FULLTEXT ... WITH PARSER ngram` 做中文全文检索 |
| 配套后端 `backend/server.py` | 用 PyMySQL 直连 seekdb，暴露 Dify HTTP Request 节点所需的 3 个接口，并执行确定性的记账规则 |

流程（`dsl/cyber-inn.yml`）：

```
开始 → 读取记忆(/api/state) → 检索传说(/api/lore/search) → 记账结算(/api/act) → LLM 老周回应 → 回复
```

记账规则是确定性的：闹事扣金币扣声望并记一笔「砸场子」，请客加声望，还账让旧账翻篇。
所以老周不会凭空编数值，他只是把 seekdb 里真实发生过的记录，用阴阳怪气的语气讲出来。

## 2. 运行环境

- seekdb 1.4.0（本仓库在 seekdb 1.3.0 Windows 构建 + 1.4.0 上验证；两者 SQL 与全文索引语法一致）
- Python 3.10+
- Dify 1.x（实测 1.18.0；DSL 按 `version: 0.1.5` 导出格式编写，导入后若提示插件缺失，按提示安装 `openai_api_compatible` 即可）
- 任意一个 OpenAI 兼容模型服务（DSL 里写的是 `langgenius/openai_api_compatible` + `MiniMax-Text-01`，换成你自己的端点与模型名）

## 3. 启动步骤

### 3.1 启动 seekdb

沿用你已有实例即可（默认 `root` / 空密码 / 端口 2881）。若没有：

```bash
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb:latest
```

连接参数全部通过环境变量覆盖，见 `backend/.env.example`：

```bash
export SEEKDB_HOST=127.0.0.1
export SEEKDB_PORT=2881
export SEEKDB_USER=root
export SEEKDB_PASSWORD=          # 占位，按你的实例填写，不要提交真实密码
export SEEKDB_DATABASE=cyber_inn
export INN_HTTP_PORT=8765
```

### 3.2 启动后端

```bash
cd dify/backend
python -m pip install -r requirements.txt
python server.py
```

首次启动会自动建库、建表，并写入 10 条客栈传说（`断魂酒坛`、`不打烊的账本` 等）。
自检：`curl http://127.0.0.1:8765/health` 返回 `{"ok": true, "engine": "OceanBase seekdb-...", "lore": 10}`。

### 3.3 导入 Dify

1. Dify → 工作室 → 导入 DSL → 选择 `dsl/cyber-inn.yml`。
2. 打开应用 → 编排，把「老周回应」节点的模型换成本地可用的模型：节点默认指向
   `langgenius/openai_api_compatible/openai_api_compatible` 供应商（DSL 里写的是
   `MiniMax-Text-01`），导入前先装好该插件并配置你的 OpenAI 兼容端点与模型名。
3. 检查三个 HTTP 节点的地址：
   - Dify 用 Docker 部署：保持 `http://host.docker.internal:8765`；Linux 需给 Dify 容器加 `extra_hosts: host.docker.internal:host-gateway`。
   - Dify 与后端同机非 Docker：改成 `http://127.0.0.1:8765`。
4. 发布应用，在「调试与预览」里按第 4 节的样例体验。

### 3.4 真实 Dify 引擎运行证据

本仓库的证据不是脚本模拟出来的：`logs/dify-engine-run.jsonl` 由真实运行的 Dify 1.18.0 引擎产出，
每个会话都带 `workflow_run_id`、`conversation_id` 和六个节点的 `node_started/node_finished` 轨迹。

复现方式（Dify 官方 docker compose + OpenAI 兼容模型 + 本后端）：

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/langgenius/dify.git
cd dify && git sparse-checkout set docker && cd docker
cp .env.example .env
# 1) 选一个向量库（示例用 qdrant；weaviate 镜像在部分网络下不可达）
#    COMPOSE_PROFILES=postgresql,qdrant / VECTOR_STORE=qdrant / QDRANT_URL=http://qdrant:6333
# 2) 让 HTTP 节点能连到宿主机的后端（api / worker 都加）
#    extra_hosts: ["host.docker.internal:host-gateway"]
# 3) 本地演示直连后端，跳过 SSRF 代理（生产环境请保留代理）
#    SSRF_PROXY_HTTP_URL= / SSRF_PROXY_HTTPS_URL=
docker compose up -d
# 4) 后台安装 langgenius/openai_api_compatible 插件，把任意 OpenAI 兼容模型
#    （本项目实测 ModelScope/MiniMax 兼容端点）配成自定义模型，模型名填进 DSL 的
#    「老周回应」节点；然后在仓库根目录执行：
DIFY_BASE_URL=http://127.0.0.1 DIFY_CONSOLE_EMAIL=... DIFY_CONSOLE_PASSWORD=... \
INN_BASE_URL=http://127.0.0.1:8765 \
python scripts/run_dify_engine.py
```

`scripts/run_dify_engine.py` 通过 Dify 控制台 API 导入 DSL、发布应用、申请 API Key，
再用 `/v1/chat-messages` 以流式方式发起三次**各自独立的新会话**（同一个玩家 `阿飞`），
把 SSE 事件写进 `logs/dify-engine-run.jsonl`：

| 会话 | 输入 | conversation_id | workflow_run_id |
| --- | --- | --- | --- |
| session1 | 我把后院的酒坛砸了 | 35e7c8fb-4c18-4fbe-b9a8-bcd54cfeb9db | 99821cab-b760-4211-88c6-3833b03663d8 |
| session2 | 你还记得我上次在你店里干了什么吗？ | 590f7669-7ae3-438b-9e9a-8a695d506bf5 | f71de93f-3f63-4353-8e92-e51f8072b165 |
| session3 | 我想用一壶桂花酿抵了旧账 | a298c7e6-045f-4d33-a95a-4cb522e9d5d6 | 907ba32f-509c-42c7-84b4-fe1aadf18883 |

三次会话的 conversation_id 互不相同，但 session2 / session3 的「读取记忆」节点返回的
`memory_hint` 仍是 `老周的记仇墙：砸场子×1`——记忆来自 seekdb，不是对话上下文。
session1 结算后金币 100→85、声望 0→-12；session3 结算后金币 53、声望 4，事件表里三条记录按时间倒序排列。

Dify 控制台侧的同一批记录也留了截图（「日志」页 → 会话详情，对话 ID 与上表一致）：

| 截图 | 内容 |
| --- | --- |
| `logs/screenshots/dify-log-list.png` | 应用「日志」页，同一玩家 `阿飞` 的三条会话记录 |
| `logs/screenshots/dify-log-session2-recall.png` | 对话 ID `590F7669-7AE3-438B-9E9A-8A695D506BF5`，新会话里老周直接翻出「砸场子×1」的旧账 |
| `logs/screenshots/dify-log-session3-tab.png` | 对话 ID `A298C7E6-045F-4D33-A95A-4CB522E9D5D6`，老周复述酒坛旧事并搬出《断魂酒坛》的规矩 |
| `logs/screenshots/dify-workflow-canvas.png` | 「编排」画布：开始 → 读取记忆 → 检索传说 → 记账结算 → 老周回应 → 直接回复 |

三张日志截图里的玩家名、对话 ID 与 `logs/dify-engine-run.jsonl` 完全对应，示例玩家 ID 全程只用 `阿飞`。

### 3.5 没有 Dify 引擎时的降级复现

`scripts/run_chatflow.py` 直接读取导出的 DSL，按 `workflow.graph` 的节点与连线顺序执行，
复刻 Dify 1.x 工作流语义：变量池（start 节点输入）、`{{#节点ID.变量#}}` 模板替换、
http-request 节点 POST JSON、llm 节点、answer 节点。每个节点的输入/输出/耗时都会写入 JSONL 轨迹。

```bash
# 后端（3.2 节）启动后：
python scripts/run_chatflow.py \
  --base-url http://127.0.0.1:8765 \
  --player-id 阿飞 \
  --action "我把后院的酒坛砸了" \
  --trace logs/chatflow-run.jsonl
```

- 设置了 `OPENAI_BASE_URL` / `OPENAI_API_KEY` 时，llm 节点真实调用模型；
- 未设置时走本地确定性 mock 回复（`--mock-llm` 可强制），不联网、不消耗额度；
- 对应证据：`logs/chatflow-run.jsonl`，仅用于快速自测，**不作为引擎运行证据**；
  引擎证据见 3.4 节的 `logs/dify-engine-run.jsonl`。

## 4. 验证样例

Dify 引擎侧的完整轨迹见 `logs/dify-engine-run.jsonl`（3.4 节，含三个独立会话的 `workflow_run_id`
与逐节点输入输出）；后端侧的分步输出见 `logs/verification.log`，逐步说明见 `samples/verification.md`。

复现一遍（会把 `阿飞` 这个玩家的数据重置后重建）：

```bash
python scripts/verify.py http://127.0.0.1:8765
```

关键预期：

1. 第 2 步「我上次喝多了，把后院的酒坛砸了」→ 金币 100→85，声望 0→-12，记仇墙出现「砸场子×1」。
2. 第 3 步重新读取状态（模拟新会话）→ 记忆里仍有「砸场子×1」，说明状态存在 seekdb 而不是对话上下文里。
3. 第 4 步检索「酒坛 规矩」→ 命中《断魂酒坛》《不收现银的规矩》。
4. 第 5 步「我把账本撕了」→ 记仇墙变成「砸场子×2」。

在 Dify 里对应的一次对话：

- 名号：`阿飞`
- 你要做的事：`老周，我又来了，今天想喝桂花酿`
- 预期：老周先翻旧账（酒坛/砸场子），再夹一条传说里的规矩，最后才谈桂花酿。

## 5. 数据与复现

- `POST /api/reset {"player_id": "..."}` 清除该玩家的全部事件与状态，用于重复验证。
- 传说数据在 `backend/server.py` 的 `LORE` 常量里，后端启动时幂等写入。
- DSL 只包含应用配置，不含数据库内容；数据由后端初始化，符合 Dify 导入导出的边界。

## 6. 已知限制

- 后端未做鉴权，定位是本地演示服务，请勿直接暴露到公网。
- 中文全文检索依赖 seekdb 的 `ngram` 分词器；若你的版本不支持，`search_lore` 会自动退回 `LIKE` 匹配，功能仍可用但排序变弱。
- 记账规则是关键词判定，用于让剧情可复现；创意表达交给 LLM。
