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
- Dify 1.x（DSL 按 `version: 0.1.5` 导出格式编写；导入后若提示模型/插件缺失，按提示绑定即可）
- 任意一个 OpenAI 兼容模型服务（DSL 里占位为 `openai / gpt-4o-mini`，导入后请替换为你自己的模型）

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
2. 打开应用 → 编排，把「老周回应」节点的模型换成本地可用的模型（其余节点无需改动）。
3. 检查三个 HTTP 节点的地址：
   - Dify 用 Docker 部署：保持 `http://host.docker.internal:8765`；Linux 需给 Dify 容器加 `extra_hosts: host.docker.internal:host-gateway`。
   - Dify 与后端同机非 Docker：改成 `http://127.0.0.1:8765`。
4. 发布应用，在「调试与预览」里按第 4 节的样例体验。

## 4. 验证样例

完整原始输出见 `logs/verification.log`，逐步说明见 `samples/verification.md`。

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
