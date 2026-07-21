# 指标、形态和评分说明

本文档说明当前代码实际使用的指标、形态、机器学习特征和评分规则。

## 1. 基础数据

基础日线结构为 `PriceVolumeBar`：

```text
name
trade_date
open
high
low
close
volume
amount
```

当前日线数据来自公开接口。腾讯接口返回的成交额没有直接使用，代码用：

```text
amount = close * volume
```

因此成交额主要用于相对筛选和流动性粗判，不应当当成精确成交金额。

## 2. 最小样本要求

- 基础指标：至少 `21` 个交易日。
- MACD：至少 `35` 个收盘价，否则 MACD 返回 `0`。
- 机器学习：至少 `45` 个交易日，且构造后样本不少于 `20`。
- 背驰：至少 `max(lookback + 35, 60)` 个交易日，默认至少 `65` 个交易日。
- 年线：至少 `250` 个交易日；年线变化需要 `251` 个交易日。

## 3. 基础指标

`calculate_indicators()` 输出 `IndicatorResult`。

价格类：

- `open`、`high`、`low`、`close`
- `return_1d`
- `return_5d`
- `return_20d`

均线类：

- `ma5`
- `ma10`
- `ma20`
- `ma20_prev`
- `ma250`
- `ma250_prev`

MACD：

- `macd_dif`
- `macd_dea`
- `macd_histogram`
- `macd_dif_prev`
- `macd_dea_prev`
- `macd_histogram_prev`

量能和流动性：

- `volume`
- `volume_ma5`
- `volume_ma20`
- `volume_ratio_5d`
- `volume_ratio_20d`
- `amount`
- `amount_ma20`

位置：

- `first_day_high`：不足 250 日时记录首日高点。
- `high_20d_prev`：前 20 日最高价。
- `low_20d_prev`：前 20 日最低价。

涨跌停近似：

- `is_limit_up`：1 日涨跌幅大于等于 `9.8%`。
- `is_limit_down`：1 日涨跌幅小于等于 `-9.8%`。

## 4. 量价形态

`detect_patterns()` 输出 `PatternResult`。

趋势位置：

- `close_above_ma20`：收盘价高于 20 日均线。
- `ma20_up`：20 日均线高于前一日 20 日均线。
- `close_above_ma250`：收盘价高于年线。
- `ma250_turning_up`：年线向上。

量价行为：

- `limit_up_low_volume`：涨停且成交量低于 5 日均量 `0.8` 倍。
- `breakout_with_volume`：突破前 20 日高点且成交量高于 5 日均量 `1.5` 倍。
- `pullback_low_volume`：单日小幅回调、缩量、仍在 20 日均线上方。
- `high_volume_drop`：单日跌幅不低于 `5%` 且放量。
- `high_volume_stagnation`：近 20 日涨幅较高，放量但价格变化不大。
- `price_up_volume_down`：上涨但缩量。
- `price_down_volume_up`：下跌且放量。
- `volume_drop_below_ma20`：下跌、放量、跌破 20 日均线。

MACD：

- `macd_golden_cross`
- `macd_golden_cross_above_zero`
- `macd_dead_cross`
- `macd_histogram_expanding`
- `macd_histogram_shrinking`

年线和新股：

- `yearline_breakout_with_volume`：放量站上年线且年线向上。
- `yearline_pullback_low_volume`：年线上方缩量回踩，距离年线不超过 `4%`。
- `first_day_high_breakout_with_volume`：不足 250 日样本放量突破首日高点。

低位反弹：

- `doji_reversal`：低位、短期回撤后出现十字星。
- `long_lower_shadow`：下影线占全天振幅不低于 `45%`，且明显长于上影线。
- `low_position_rebound`：接近前 20 日低点，并出现十字星或长下影。

风险：

- `low_liquidity`：20 日平均成交额低于 `5000` 万元。
- `high_20d_return`：近 20 日涨幅超过 `30%`。

## 5. 机器学习特征

`ml_model.py` 不依赖第三方库，使用纯 Python 实现。

每个历史样本使用 14 个特征：

1. 1 日收益率。
2. 5 日收益率。
3. 10 日收益率。
4. 20 日收益率。
5. 收盘价相对 MA5。
6. 收盘价相对 MA10。
7. 收盘价相对 MA20。
8. MA20 相对前一日 MA20。
9. 成交量 / 5 日均量。
10. 成交量 / 20 日均量。
11. 收盘价相对前 20 日高点。
12. 收盘价相对前 20 日低点。
13. 10 日波动率。
14. 20 日波动率。

样本标签：

- 1 日模型：下一交易日收益率不低于 `0`，且未来最大回撤大于 `-6%`。
- 5 日模型：未来 5 日收益率不低于 `1.2%`，且期间最大回撤大于 `-6%`。

## 6. 支持算法

```text
knn                  KNN 相似样本
weighted_knn         距离加权 KNN
logistic_regression  逻辑回归
time_decay_knn       时间衰减 KNN（5日）
robust_ensemble      稳健集成模型（5日）
```

KNN 样本数：

```text
neighbor_count = round(sqrt(sample_count))
范围限制在 7 到 21
偶数时加 1
```

概率会向历史基准胜率收缩，避免样本少时过度自信。

## 7. 买入评分

买入评分由 `generate_advice()` 生成。

基础分：

```text
round(机器学习历史达标占比 * 100)
```

主要加分：

- 资金流正面：由资金流模块提供，最高 `+20`。
- 下跌背驰候选：由背驰模块提供，最高 `+20`。
- MACD 零轴上方金叉：`+6`。
- 普通 MACD 金叉：`+3`。
- MACD 柱体扩大：`+3`。
- 低位十字星：`+4`。
- 长下影线：`+4`。
- 低位止跌反弹：`+6`。
- 放量突破首日高点：`+8`。
- 放量站上年线：`+10`。
- 年线上方缩量回踩：`+12`。
- 年线向上拐头：`+4`。

主要扣分：

- 资金流负面：由资金流模块提供，最低 `-20`。
- MACD 死叉：`-6`。
- MACD 柱体走弱：`-3`。
- 收盘价低于年线：`-8`。

评分最终限制在 `0` 到 `100`。

## 8. 建议动作

```text
BUY    score >= 60
WATCH  score >= 52
HOLD   score >= 45
AVOID  score < 45
```

建议动作只代表筛选优先级，不代表确定收益。

## 9. 输出解释

系统输出分为：

- `reasons`：支持当前建议的核心理由。
- `evidence`：可核对的数据证据。
- `risks`：主要风险提示。
- `observations`：后续观察点。

文档、CLI 和 Web 都应保持这个解释结构，避免只展示一个分数。
