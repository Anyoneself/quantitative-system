# CLI 使用说明

本文档按当前代码实现说明 CLI 功能。推荐入口是项目脚本 `./scripts/quant`，它会自动设置 `PYTHONPATH`。

## 1. 命令总览

```bash
./scripts/quant advise <symbol>
./scripts/quant sell <symbol> --cost-price <price>
./scripts/quant scan --symbols <symbols> --once
./scripts/quant entry-scan --symbols <symbols> --once
./scripts/quant evaluate <symbol>
./scripts/quant backtest <symbol>
./scripts/quant web --port 8000
```

股票代码必须是 6 位数字。

## 2. `advise` 单股分析

用途：分析一只股票当前是否适合买入观察。

```bash
./scripts/quant advise 600519
./scripts/quant advise 600519 --algorithm robust_ensemble
./scripts/quant advise 600519 --format json
```

参数：

- `symbol`：股票代码，必填。
- `--algorithm`：机器学习算法，默认 `robust_ensemble`。
- `--format`：`text` 或 `json`，默认 `text`。

输出包括：

- 股票代码、名称、分析日期、数据截止日。
- 买入建议动作：`BUY`、`WATCH`、`HOLD`、`AVOID`、`NO_DATA`。
- 综合评分。
- 核心理由。
- 数据证据。
- 风险提示。
- 关键指标。
- 机器学习预测。
- 资金流向。
- 背驰信号。
- 后续观察点。

动作映射：

```text
BUY    综合评分 >= 60
WATCH  综合评分 >= 52
HOLD   综合评分 >= 45
AVOID  综合评分 < 45
```

## 3. `sell` 持仓卖出风险

用途：结合持仓成本判断是否继续持有、减仓、止盈或止损。

```bash
./scripts/quant sell 601988 --cost-price 5.20
./scripts/quant sell 601988 --cost-price 5.20 --quantity 1000 --max-loss-rate 0.08 --target-profit-rate 0.20
./scripts/quant sell 601988 --cost-price 5.20 --format json
```

参数：

- `--cost-price`：持仓成本价，必填且必须大于 0。
- `--quantity`：持仓数量，可选，仅用于提示仓位风险。
- `--max-loss-rate`：最大可承受亏损比例，默认 `0.08`。
- `--target-profit-rate`：目标止盈比例，默认 `0.20`。
- `--format`：`text` 或 `json`。

动作类型：

```text
HOLD         继续持有
REDUCE       减仓观察
TAKE_PROFIT  止盈或分批减仓
STOP_LOSS    止损
NO_POSITION  仅技术风险提示
```

## 4. `scan` 股票池扫描

用途：扫描自选股票池，输出评分靠前的候选股票。

```bash
./scripts/quant scan --pool-file config/stock_pool.example.txt --once
./scripts/quant scan --symbols 601988,600519,600036 --once
./scripts/quant scan --pool-file config/stock_pool.example.txt --top 10 --min-score 52 --interval 600
```

参数：

- `--symbols`：逗号分隔股票代码。
- `--pool-file`：股票池文件，每行一个股票代码，支持 `#` 注释。
- `--algorithm`：机器学习算法。
- `--top`：输出前 N 只，默认 `10`。
- `--min-score`：最低综合评分，默认 `52`。
- `--interval`：循环扫描间隔秒数，默认 `600`。
- `--once`：只扫描一次。

排序规则：

```text
综合评分优先，其次历史达标占比。
```

## 5. `entry-scan` 入场信号扫描

用途：筛选下跌背驰候选，同时要求成交额较低。

```bash
./scripts/quant entry-scan --pool-file config/stock_pool.example.txt --once
./scripts/quant entry-scan --symbols 601988,600519 --max-amount 80000000 --max-amount-ratio 0.7 --min-confidence 50 --once
```

默认条件：

- 出现 `下跌背驰候选` 或 `多空背驰分歧` 中的下跌背驰部分。
- 背驰置信度不低于 `40`。
- 当日成交额不高于 `50000000`。
- 当日成交额 / 20 日平均成交额不高于 `0.8`。

参数：

- `--max-amount`：当日成交额上限，设置为 `0` 表示不限制绝对成交额。
- `--max-amount-ratio`：当日成交额相对 20 日平均成交额上限。
- `--min-confidence`：最低背驰置信度，范围 `0` 到 `100`。

排序规则：

```text
背驰置信度优先，成交额比例越低越靠前，综合评分作为辅助。
```

## 6. `evaluate` 样本外验证

用途：按时间顺序扩展训练窗口，在验证段选择阈值，冻结后只在测试段报告表现。

```bash
./scripts/quant evaluate 600519
./scripts/quant evaluate 600519 --algorithm robust_ensemble
./scripts/quant evaluate 600519 --format json
```

输出包括冻结阈值、测试段 Precision、基准达标率、Lift、Wilson 95% 区间、Coverage、平均净收益、Brier、样本数和日期范围。测试段结果不能用于继续调参。

## 7. `backtest` 单股回测

用途：验证买入评分阈值和卖出风险阈值在历史数据中的表现。

```bash
./scripts/quant backtest 600519
./scripts/quant backtest 600519 --buy-score 60 --sell-risk 45 --max-position-rate 0.95
./scripts/quant backtest 600519 --format json
```

主要参数：

- `--initial-cash`：初始资金，默认 `100000`。
- `--buy-score`：买入评分阈值，默认 `60`。
- `--sell-risk`：卖出风险阈值，默认 `45`。
- `--max-position-rate`：最大仓位比例，默认 `0.95`。
- `--commission-rate`：佣金比例，默认 `0.0003`。
- `--min-commission`：最低佣金，默认 `5`。
- `--stamp-duty-rate`：卖出印花税比例，默认 `0.0005`。
- `--slippage-rate`：滑点比例，默认 `0.0005`。
- `--no-liquidate-at-end`：期末保留持仓；默认按最后收盘价计滑点和费用后平仓。

回测不会使用未来数据：信号在收盘后生成，下一交易日开盘成交。

## 8. `web` 本地 Web

```bash
./scripts/quant web --port 8000
./scripts/quant web --port 0
```

Web 默认监听 `127.0.0.1`。如果 `8000` 被占用，会尝试 `8001` 到 `8010`。

## 9. 算法选项

```text
knn                  KNN 相似样本，下一交易日
weighted_knn         距离加权 KNN，下一交易日
logistic_regression  逻辑回归，下一交易日
time_decay_knn       时间衰减 KNN，未来 5 日
robust_ensemble      稳健集成模型，未来 5 日
```

`robust_ensemble` 是默认算法，它混合时间衰减 KNN、逻辑回归和技术先验。

## 10. 常见失败

- 股票代码不是 6 位数字：CLI 返回参数错误。
- 行情接口失败：单股分析返回 `NO_DATA` 或回测报错。
- 样本不足：少于 21 个交易日无法计算基础指标；少于 45 个交易日无法形成机器学习预测。
- 公开接口限流：调大 `PUBLIC_REQUEST_INTERVAL_SECONDS` 后重试。
