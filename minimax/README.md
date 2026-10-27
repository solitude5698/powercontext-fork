# SDD 工程上下文模块（MiniMax 赛题）

围绕规格驱动开发（Spec-Driven Development）的工程上下文模块：给定一个开发任务，从规格、代码、测试中选出当前有效的信息，
交给 MiniMax 完成代码修改，跑完测试后把任务状态写入 PowerContext，模块重启后仍能读取并复用。

- 业务工程：`demo_project/`（Python 包 `order_pricing`，pytest 覆盖）
- 上下文模块：`sdd/`（CLI 入口 `python -m sdd`）
- 验证样例：`samples/task-01/`
- 运行证据：`evidence/task-01/`

## 1. 依赖

| 依赖 | 版本 | 用途 |
| --- | --- | --- |
| Python | 3.11+ | 运行环境 |
| seekdb 或 OceanBase | seekdb 1.3.0 已验证 | 存储规格/代码/测试并做全文检索 |
| pyseekdb | 1.4.0.post1（可选） | 用嵌入式 seekdb 时使用；本说明默认连 seekdb 服务 |
| mysql-connector-python | 26.10.0 | 通过 MySQL 协议访问 seekdb |
| powercontext[cli,server] | 1.4.1 | 任务状态保存与读取 |
| requests | 2.34.2 | 调用 MiniMax 与 PowerContext HTTP API |
| pytest | 9.1.1 | 业务工程测试 |

```bash
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. 准备 seekdb

二选一，任选一种能连上即可：

```bash
# 方式 A：已有 seekdb 服务（本例）——监听 2882，root 空密码
# 方式 B：Docker
docker run -d --name seekdb -p 2882:2882 oceanbase/seekdb:latest
```

模块会自动执行 `CREATE DATABASE IF NOT EXISTS sdd_context` 并建表，无需手工初始化。

## 3. 配置

```bash
cp .env.example .env
```

`.env` 中需要填三项真实值（其余保持默认）：

| 变量 | 说明 |
| --- | --- |
| `SEEKDB_HOST` / `SEEKDB_PORT` / `SEEKDB_USER` / `SEEKDB_PASSWORD` | seekdb 连接信息 |
| `MINIMAX_API_KEY` | MiniMax **Token Plan 订阅 Key**（不是按量付费 Key），填写后不要提交到仓库 |
| `POWERCONTEXT_URL` | PowerContext Server 地址，默认 `http://127.0.0.1:8000` |

`.env` 已在 `.gitignore` 中忽略；仓库里只保留 `.env.example` 占位值。

## 4. 启动 PowerContext

```bash
powercontext server run          # 默认 SQLite，监听 127.0.0.1:8000
curl --fail http://127.0.0.1:8000/health/live
```

## 5. 初始化数据

```bash
python -m sdd index
# 已写入 seekdb：21 条（{'spec': 5, 'code': 6, 'test': 10}）
```

`index` 会重建 `materials` 表内容：`specs/*.md` 按标题切分、`src/**/*.py` 按函数/类/模块常量切分、
`tests/test_*.py` 按测试函数切分，并为 `content` 建 ngram 全文索引。

## 6. 调用入口

| 命令 | 作用 | 输出 |
| --- | --- | --- |
| `python -m sdd index` | 重建索引 | 写入条数 |
| `python -m sdd context --task samples/task-01/task.md` | 为任务选择上下文 | 规格/代码/测试来源 + PowerContext 历史状态，写入 `evidence/task-01/context.json` |
| `python -m sdd fix --task samples/task-01/task.md` | 上下文 → MiniMax 生成补丁 → 应用 → 前后测试 → 保存状态 | 修改前后测试退出码与证据文件 |
| `python -m sdd status --task samples/task-01/task.md` | 模块重启后读取 PowerContext 状态 | 已完成事项、待办、验证结果 |

## 7. 验证样例（task-01）

任务：给 `order_pricing` 增加 `gold_plus` 客户等级（≥1000 元 15%，<1000 元按 gold 规则），
且不得改变 `silver` / `gold` 既有行为。样例说明见 `samples/task-01/task.md`，期望命中的来源见
`samples/task-01/expected-context.md`。

```bash
python -m sdd index
python -m sdd context --task samples/task-01/task.md
python -m sdd fix --task samples/task-01/task.md
python -m sdd status --task samples/task-01/task.md
```

`fix` 在 `.work/task-01/` 的工程副本上执行，不会修改 `demo_project/` 本身，因此可反复重跑。

## 8. 已知限制

- 检索使用 seekdb 的 ngram 全文索引 + 标识符 LIKE 匹配，适合当前规模；仓库规模变大时应改为向量检索或混合检索。
- `fix` 只接受 MiniMax 返回的 unified diff，由 `sdd/patch.py` 按上下文匹配落地，容忍行号/行数偏差；
  找不到匹配或试图改动 `tests/` 时直接报错，不会静默改写代码。
- PowerContext 使用默认 SQLite 存储，未启用生成/嵌入模型，因此只做显式 Memory 与 PreparedContext，不做 Topic 自动演化。
- 同一任务重复运行时，`evidence/task-01/` 下的文件会被覆盖，`powercontext-calls.jsonl` 与 `minimax-calls.jsonl` 为追加写入；
  PowerContext 中该任务的旧 decision 会被置为 `forgotten`，只保留最近一次运行结论。
- `python -m sdd status` 的检索查询必须保持在单行内（PreparedContext 对多行 query 返回空内容），故只使用 `task_id + 标题`。

## 9. 重置

```bash
rm -rf .work evidence/task-01       # 清掉工作副本与证据
python -m sdd index                 # 重建索引
```

PowerContext 中的数据需在 Dashboard 或通过 API 清理；如需完全从空状态开始，删除 PowerContext 的 SQLite 文件后重启 Server。
