# 验证样例：赛博客栈·老周的记性

验证目标：证明「老周的记忆」不是对话上下文里的临时状态，而是真实写入 seekdb、在后续会话里读回来的数据；
同时证明传说检索走的是 seekdb 的中文全文索引。

## 初始条件

- seekdb 中数据库 `cyber_inn` 已有 10 条传说，玩家 `阿飞` 无记录（`POST /api/reset {"player_id": "阿飞"}` 可随时恢复）。
- 后端 `http://127.0.0.1:8765` 运行中。

## 操作步骤与预期

| # | 操作 | 输入 | 预期 |
| --- | --- | --- | --- |
| 1 | 重置 | `POST /api/reset {"player_id":"阿飞"}` | 返回 ok，事件清空 |
| 2 | 前情 | `POST /api/act {"player_id":"阿飞","action":"我上次喝多了，把后院的酒坛砸了"}` | 金币 -15、声望 -12、记仇墙「砸场子×1」 |
| 3 | 新会话读记忆 | `POST /api/state {"player_id":"阿飞"}` | 金币 85、声望 -12、记仇墙仍为「砸场子×1」 |
| 4 | 检索传说 | `POST /api/lore/search {"query":"酒坛 规矩","k":3}` | 命中《不收现银的规矩》《断魂酒坛》 |
| 5 | 本次行为 | `POST /api/act {"player_id":"阿飞","action":"我趁老周转身，把账本撕了"}` | 金币 70、声望 -24、记仇墙「砸场子×2」 |
| 6 | 结算后读回 | `POST /api/state {"player_id":"阿飞"}` | 与第 5 步一致，且最近事件按时间倒序列出两条 |
| 7 | 检索传说 | `POST /api/lore/search {"query":"欠账 还清 名字","k":2}` | 命中《不打烊的账本》 |

## 实际结果

完整原始输出：`logs/verification.log`（由 `scripts/verify.py` 生成）。

- 第 2 步返回 `gold_delta=-15, rep_delta=-12, tag=砸场子`；
- 第 3 步重新建立会话读取状态，`memory_hint = "老周的记仇墙：砸场子×1"`；
- 第 4 步返回两条传说，`断魂酒坛` 得分 0.951；
- 第 5 步返回 `gold=70, reputation=-24, rounds=2, 记仇墙=砸场子×2`；
- 第 7 步返回《不打烊的账本》，得分 3.51。

## seekdb 内的实际数据（验证后直接查询）

```sql
SELECT player_id, gold, reputation, rounds FROM players;
-- 阿飞 | 70 | -24 | 2

SELECT tag, COUNT(*) FROM events WHERE player_id = '阿飞' GROUP BY tag;
-- 砸场子 | 2

SELECT id, action, tag, gold_delta, rep_delta FROM events WHERE player_id = '阿飞' ORDER BY id;
-- 1 | 我上次喝多了，把后院的酒坛砸了 | 砸场子 | -15 | -12
-- 2 | 我趁老周转身，把账本撕了       | 砸场子 | -15 | -12

SELECT title FROM lore ORDER BY id;
-- 断魂酒坛、不打烊的账本、招牌上的雨、说书人的空椅、后厨的猫、
-- 三更的马蹄、不收现银的规矩、酒杯里的月亮、墙上的钉子、老周的旧伤
```

## 在 Dify 里按同样步骤体验

1. 名号填 `阿飞`，第一句输入 `我上次喝多了，把后院的酒坛砸了`。
   预期：老周阴阳怪气地提到后院酒坛，并带出《断魂酒坛》里「摔坛者三年不许点桂花酿」的规矩。
2. 刷新页面或新开会话，名号仍填 `阿飞`，输入 `老周，我又来了，今天想喝桂花酿`。
   预期：即使对话上下文已清空，老周仍会先翻出「砸场子」的旧账，再决定给不给你酒。
3. 名号换成一个新名字（如 `小满`），输入同样的话。
   预期：老周表示第一次见你，不会提起任何旧账——证明记忆是按玩家存在 seekdb 里的。

第 2、3 步的差异就是本作品的技术核心：跨会话记忆来自 seekdb，而不是 Dify 的会话变量。
