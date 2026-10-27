# 技术说明（一页）

## 模块结构

```
sdd/
  cli.py        命令入口：index / context / fix / status
  indexer.py    规格按标题、代码按符号、测试按用例切分为可检索单元
  db.py         seekdb 存储与检索（materials / runs 两张表）
  selector.py   任务 → 上下文（规格 + 代码 + 测试 + 历史状态）→ 提示词
  mini.py       MiniMax Chat Completions 客户端，落盘调用记录
  patch.py      解析 unified diff 并按上下文定位改写文件（容忍行号/行数偏差）
  pc.py         PowerContext HTTP 客户端：Scope / Source / Memory / PreparedContext
demo_project/   可运行业务工程：order_pricing + 10 个 pytest 用例
```

## 上下文选择方式

1. **切分与索引**：规格按 Markdown 标题切段；源码按 `ast` 抽出函数、类、模块级常量；测试按 `test_*` 函数切分。
   每条单元带 `kind / path / symbol / title / content`，写入 seekdb 的 `materials` 表，`content` 建 ngram 全文索引。
2. **检索**：任务文本先走 `MATCH(content) AGAINST(... IN NATURAL LANGUAGE MODE)` 拿到全文相关度，
   再用任务中的英文标识符（如 `gold_plus`、`discount_rate`）做 `LIKE` 补充召回，两路得分相加后按 kind 配额截断。
   查询语句与命中 id 写入 `evidence/task-01/seekdb-queries.log`。
3. **上下文组装**：命中的规格段落全文、命中符号源码、相关测试用例，加上被选中代码文件的完整内容，
   一并进入提示词；再从 PowerContext 读回该任务的历史状态（已完成 / 待办 / 验证结果）作为「已发生的事实」。
4. **来源可核对**：`context` 输出里每个来源都是 `路径#符号` 形式，能够在仓库中直接定位；规格更新后重新 `index`，
   检索结果随之变化，不存在写死的答案。

## 三项技术的分工

| 技术 | 承担的作用 | 证据 |
| --- | --- | --- |
| MiniMax | 读取上下文并生成实现该任务的 unified diff，补丁由模块 `patch.py` 落地 | `evidence/task-01/minimax-calls.jsonl`（含请求消息、响应、耗时、usage） |
| seekdb | 存储规格/代码/测试三类材料与其全文索引，检索结果直接决定上下文 | `evidence/task-01/seekdb-queries.log`、`runs` 表 |
| PowerContext | 每次运行把任务进展与验证结果写成 Source 与 decision Memory；重启后通过 PreparedContext 读回并复用 | `evidence/task-01/powercontext-calls.jsonl` |

## 补丁落地

模型给出的 diff 行号与 `@@` 计数经常不准，`patch.py` 不信任这两项：按 hunk 内容在目标文件中
逐行匹配（先按行号附近找，再全文找，比较时忽略行尾空白），命中后整段替换；`tests/` 下的路径
被列为受保护路径，补丁一旦试图改动测试文件就直接报错，避免「改测试而不是改代码」。

## 状态复用

每次运行先 `forget_previous(task_id)`：列出该任务的 active decision Memory，把旧记录置为
`forgotten`，再写入本次 Source 与 decision，读回时不会混入历史失败记录；Source 使用
`<task_id>-<run_id>` 作为身份，保留每次运行的完整摘要。

## 验证设计

- 目标测试 `tests/test_gold_plus.py` 在修改前以 `ValueError: unknown customer tier: gold_plus` 失败，
  属于功能缺失，不是环境错误；修改后同一组用例全部通过。
- 回归测试 `tests/test_rules.py` 覆盖 silver、gold、异常与舍入，修改前后都必须通过。
- `fix` 在 `.work/task-01/` 的副本上作业，`demo_project/` 始终保持「修改前」状态，便于评审复现。
- 测试集在修改前后保持一致，不删断言、不跳过用例。

## 边界

- 检索是全文 + 标识符级别，不理解语义同义词；规格文档规模变大后需要向量或混合检索。
- 补丁只在 `demo_project` 的副本上应用，不处理跨仓库或二进制文件。
- PowerContext 仅启用显式 Memory 与 PreparedContext，不依赖生成/嵌入模型。
