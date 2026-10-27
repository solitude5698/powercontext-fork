# 任务 01 期望的上下文

`python -m sdd context --task samples/task-01/task.md` 的输出必须至少命中下列来源（顺序可变）：

| 类型 | 来源 | 说明 |
| --- | --- | --- |
| spec | `specs/pricing.md#2. 基础折扣` | 给出 gold_plus 的 15% 基础折扣 |
| spec | `specs/pricing.md#3. 大额订单规则` | 给出 ≥1000 不叠加、<1000 按 gold 计算的规则 |
| spec | `specs/pricing.md#5. 兼容性要求` | 说明不得改变 silver / gold 行为 |
| code | `src/order_pricing/rules.py#discount_rate` | 需要修改的符号 |
| code | `src/order_pricing/rules.py#TIER_DISCOUNTS` | 需要新增的等级映射 |
| test | `tests/test_gold_plus.py#test_gold_plus_large_order_uses_fifteen_percent` | 目标测试，改前失败 |
| test | `tests/test_rules.py#test_gold_gets_bulk_bonus_at_threshold` | 回归测试，改前改后都必须通过 |

同时输出必须包含 PowerContext 中记录的上一次任务状态（已完成事项、待办事项、已有验证结果）；首次运行没有任何历史状态时，明确输出「无历史状态」。
