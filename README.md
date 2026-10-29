# 2026 OceanBase 数据库大赛 · 生态伙伴赛题作品集

每个伙伴方向一个目录，各自带 README、可运行代码与验证产物。

| 目录 | 赛题 | 说明 |
| --- | --- | --- |
| [`dify/`](dify/README.md) | 600076 Dify | 赛博客栈：Dify Chatflow + seekdb 记忆，跨会话翻旧账 |
| [`minimax/`](minimax/) | 600077 MiniMax | SDD 上下文模块，记忆落 seekdb |
| [`qoder/`](qoder/) | 600078 Qoder | 周报匠 WeeklySmith |
| [`stepfun/`](stepfun/) | 600073 StepFun | 浏览器端向量化 + seekdb 混合检索知识库 |
| [`gaode/`](gaode/) | 600074 高德 | 附近地点推荐 Skill |

## Dify 赛题（600076）快速导览

题目要求用 Dify 做一个好玩的智能应用，并能证明**新 Dify 会话能召回此前写入的记忆**。

- 应用：`dify/dsl/cyber-inn.yml`（Chatflow：开始 → 读取记忆 → 检索传说 → 记账结算 → 老周回应 → 直接回复）
- 后端：`dify/backend/server.py`（seekdb 存玩家状态、事件与 10 条客栈传说）
- 引擎运行证据：`dify/logs/dify-engine-run.jsonl`，三个**各自独立**的会话（同一玩家 `阿飞`），
  每条都带 Dify 生成的 `conversation_id`、`workflow_run_id` 和逐节点 `node_started/node_finished` 轨迹；
  session2/session3 在没有任何对话上下文的情况下，仍从 seekdb 读到「记仇墙：砸场子×1」。
- 控制台截图：`dify/logs/screenshots/`，对话 ID 与上面 JSONL 一一对应。
- 复现步骤与详细说明见 [`dify/README.md`](dify/README.md) 第 3.4 节。
