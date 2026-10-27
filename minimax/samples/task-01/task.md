# 任务 01：新增 gold_plus 客户等级

## 要改什么

订单折扣服务需要支持新的客户等级 `gold_plus`：

- 金额 ≥ 1000 元时，折扣率 15%。
- 金额 < 1000 元时，按 `gold` 的规则计算：≥ 500 元为 12%，否则 10%。
- 公开接口 `discount_rate(tier, amount)`、`final_price(amount, tier)` 的签名不变，异常类型不变。

## 哪些原有行为必须保持

- `silver` 恒为 5%，不参与大额加成。
- `gold`：< 500 元 10%，≥ 500 元 12%。
- 未知等级、负数金额仍然抛 `ValueError`。
- 应付金额四舍五入保留两位小数。

## 验收方式

```bash
cd minimax/demo_project
python -m pytest -q
```

- 修改前：`tests/test_gold_plus.py` 的 4 个用例失败（`ValueError: unknown customer tier: gold_plus`），`tests/test_rules.py` 的 6 个回归用例通过。
- 修改后：10 个用例全部通过。
