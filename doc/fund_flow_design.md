# 资金流向模块说明

本文档说明当前资金流模块的实现和评分接入方式。

## 1. 定位

资金流模块用于辅助判断资金是否配合买入或增加卖出风险。

它不是单独买卖信号，只作为：

- 买入评分调整因子。
- 卖出风险调整因子。
- 背驰可信度辅助因子。

资金流失败时，主分析继续运行。

## 2. 数据来源

当前使用东方财富单股日级资金流公开接口。

入口：

```text
src/data/fund_flow.py
```

统一调用：

```text
fetch_fund_flow_bars(symbol, limit=20)
```

接口返回后解析为 `FundFlowBar`：

```text
trade_date
main_net_inflow
super_large_net_inflow
large_net_inflow
medium_net_inflow
small_net_inflow
main_net_inflow_ratio
```

代码对同一进程内的 `(symbol, limit)` 做内存缓存。

## 3. 分析结果

`analyze_fund_flow()` 输出 `FundFlowResult`：

```text
latest_main_net_inflow
latest_main_net_inflow_ratio
main_inflow_3d
main_inflow_5d
main_inflow_10d
positive_days_5d
positive_days_10d
super_large_inflow_5d
retail_outflow_5d
fund_flow_score_adjustment
signal
explanation
```

资金流信号：

```text
持续流入：5 日和 10 日主力净流入都为正，且近 5 日至少 3 天净流入
持续流出：5 日和 10 日主力净流入都为负，且近 5 日净流入天数不超过 2
资金分歧：其他情况
```

## 4. 买入评分调整

资金流调整分范围：

```text
-20 到 +20
```

加分规则：

- 5 日主力净流入为正：`+4`。
- 10 日主力净流入为正：`+4`。
- 近 5 日至少 3 天主力净流入：`+4`。
- 当日主力净流入占比大于 `3%`：`+5`。
- 近 5 日超大单净流入为正：`+4`。
- 主力 5 日净流入为正且小单 5 日净流出：`+3`。

扣分规则：

- 5 日主力净流入不为正：`-5`。
- 10 日主力净流入不为正：`-5`。
- 近 10 日净流入天数不超过 3：`-3`。
- 当日主力净流入占比小于 `-3%`：`-6`。

## 5. 卖出风险接入

卖出模块中：

- `持续流出` 增加卖出风险 `+10`。
- 5 日主力净流出且放量跌破 20 日均线，额外增加 `+15`。
- 5 日主力净流入且缩量回调，降低卖出风险 `-6`。

## 6. 背驰接入

背驰模块中：

- 低位接近新低时，如果资金流不是 `持续流出`，下跌背驰可信度提高。
- 高位接近新高时，如果资金流是 `持续流出`，上涨背驰风险提高。

## 7. 输出位置

CLI：

- `advise` 输出“资金流向”。
- `sell` 输出“资金流卖出风险”。

Web：

- `POST /api/advise` 返回 `fund_flow`。
- `POST /api/sell-advice` 返回 `fund_flow_risk`，并在 `sell_advice.fund_flow` 中保留完整对象。
- 前端单股页面展示资金信号、当日主力净流入、5 日和 10 日主力净流入。

## 8. 使用边界

- 免费公开资金流口径可能变化。
- 单日资金流容易受尾盘异动影响。
- 资金流不能替代价格趋势和风险控制。
- 当前资金流没有持久化缓存，进程重启后会重新请求。
- 当前资金流接口没有独立重试逻辑，失败时降级为不可用。

## 9. 建议后续优化

1. 增加资金流接口重试和限速。
2. 增加缓存过期时间。
3. 增加字段完整性检查测试。
4. 回测资金流调整分是否真实提高信号质量。
