# Quantitative-System

Quantitative-System 是一个面向 A 股的本地量价分析工具。它使用免登录公开行情接口，围绕单股买入观察、持仓卖出风险、股票池扫描、底背离低成交额筛选和单股回测提供辅助决策。

本项目不是自动交易系统，不连接券商账户，不下单，也不承诺盈利。它更适合作为“候选股筛选器”和“交易前检查清单”：先用规则和历史相似样本缩小范围，再由使用者结合市场环境、仓位和纪律做最终判断。

## 当前能力

- 单股分析：输出买入评分、建议动作、核心理由、数据证据、风险提示和后续观察点。
- 持仓卖出：输入成本价后，输出卖出风险评分、继续持有/减仓/止盈/止损建议。
- 股票池扫描：对自选股票池定时或单次扫描，输出评分最高的候选股票。
- 入场扫描：筛选“下跌背驰候选 + 成交额较低”的股票。
- 全 A 扫描：Web 端逐只分析公开 A 股列表，实时显示 Top 10。
- 单股回测：用历史日线验证买入评分和卖出风险阈值的表现。
- 本地 Web：提供单股分析、持仓卖出判断、K 线/成交量图和全 A 扫描界面。

## 不做什么

- 不进行实盘交易。
- 不保存或提交大体量行情数据。
- 不使用登录、付费、验证码或绕过访问控制的数据源。
- 不保证信号一定盈利。
- 不把机器学习概率解释为收益率。

## 项目结构

```text
quantitative-system/
├── config/                 # 示例股票池
├── doc/                    # 当前实现文档
├── scripts/quant           # 推荐启动脚本
├── src/
│   ├── analysis/           # 指标、形态、模型、评分、扫描、卖出判断
│   ├── backtest/           # 单股回测
│   ├── data/               # 公开行情和资金流数据源
│   ├── output/             # CLI 输出格式化
│   ├── web.py              # 本地 Web 服务和 API
│   └── web_static/         # 前端静态文件
└── tests/                  # 单元测试
```

## 快速启动

推荐使用项目内置脚本，它会自动设置 `PYTHONPATH`：

```bash
./scripts/quant advise 600519
```

启动 Web：

```bash
./scripts/quant web --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000
```

如果 `8000` 被占用，服务会自动尝试 `8001` 到 `8010`。也可以使用：

```bash
./scripts/quant web --port 0
```

## CLI 命令

### 单股买入观察

```bash
./scripts/quant advise 600519
./scripts/quant advise 600519 --algorithm robust_ensemble
./scripts/quant advise 600519 --format json
```

支持算法：

- `robust_ensemble`：稳健集成模型，预测未来 5 个交易日是否达标。
- `time_decay_knn`：时间衰减 KNN，预测未来 5 个交易日是否达标。
- `knn`：普通 KNN，预测下一交易日是否达标。
- `weighted_knn`：距离加权 KNN，预测下一交易日是否达标。
- `logistic_regression`：轻量逻辑回归，预测下一交易日是否达标。

### 持仓卖出风险

```bash
./scripts/quant sell 601988 --cost-price 5.20
./scripts/quant sell 601988 --cost-price 5.20 --quantity 1000 --max-loss-rate 0.08 --target-profit-rate 0.20
./scripts/quant sell 601988 --cost-price 5.20 --format json
```

### 股票池扫描

```bash
./scripts/quant scan --pool-file config/stock_pool.example.txt --once
./scripts/quant scan --symbols 601988,600519,600036 --once
./scripts/quant scan --pool-file config/stock_pool.example.txt --interval 600 --top 10 --min-score 52
```

### 底背离低成交额入场扫描

```bash
./scripts/quant entry-scan --pool-file config/stock_pool.example.txt --once
./scripts/quant entry-scan --pool-file config/stock_pool.example.txt --max-amount 80000000 --max-amount-ratio 0.7 --min-confidence 50 --once
```

默认筛选条件：

- 出现下跌背驰候选。
- 背驰置信度不低于 `40`。
- 当日成交额不高于 `5000` 万元。
- 当日成交额 / 20 日平均成交额不高于 `0.8`。

### 单股回测

```bash
./scripts/quant backtest 600519
./scripts/quant backtest 600519 --buy-score 60 --sell-risk 45 --initial-cash 100000
./scripts/quant backtest 600519 --format json
```

回测假设：

- 收盘后生成信号，下一交易日开盘成交。
- 买入包含佣金和滑点。
- 卖出包含佣金、印花税和滑点。
- 涨停开盘不买入，跌停开盘不卖出。
- 按 `100` 股整手交易。

## Web 功能

Web 端支持两个模式：

- 单股分析：输入股票代码和算法，展示买入评分、卖出评分、资金流、背驰、关键指标、风险提示和最近 90 个交易日图表。
- 全 A 扫描：从公开股票列表逐只分析，剔除创业板、科创板、北交所等代码段后，实时保留当前评分最高的 Top 10。

全 A 扫描是单轮任务。完成后不会自动循环，需要手动重新启动。

## 数据来源

当前使用公开免登录接口：

- 日线行情：腾讯接口优先，新浪接口兜底。
- 股票列表：新浪公开 A 股列表。
- 资金流：东方财富单股日级资金流。

行情模块包含超时、重试和限速。资金流模块在单次进程内做简单内存缓存；资金流失败不会阻断主分析。

## 环境变量

```text
PUBLIC_DATA_USER_AGENT              请求 User-Agent
PUBLIC_DATA_TIMEOUT_SECONDS         请求超时时间，默认 8 秒
PUBLIC_DATA_RETRIES                 日线行情重试次数，默认 2
PUBLIC_DATA_RETRY_BASE_SECONDS      重试基础等待秒数，默认 1
PUBLIC_REQUEST_INTERVAL_SECONDS     日线和列表请求限速间隔，默认 0.25
PUBLIC_KLINE_SOURCE                 日线优先数据源，tencent 或 sina
PUBLIC_STOCK_LIST_PAGE_SIZE         股票列表分页大小，默认 80
PUBLIC_KLINE_DAYS                   拉取日线数量，默认 260
MARKET_SCAN_WORKERS                 Web 全 A 扫描并发数，默认 4
MARKET_TASK_TIMEOUT_SECONDS         单只股票扫描超时，默认 30
```

如果公开接口限流，可以调大：

```bash
PUBLIC_REQUEST_INTERVAL_SECONDS=1 ./scripts/quant web --port 8000
```

## 评分概览

买入评分以机器学习概率为基础，再叠加或扣减：

- 资金流调整分：范围 `-20` 到 `+20`。
- 背驰买入调整分：范围 `0` 到 `+20`。
- MACD、低位反弹、年线突破、强势股回档等规则分。
- 跌破年线、MACD 死叉、放量下跌、高位滞涨、流动性不足等风险项。

动作映射：

```text
BUY    score >= 60
WATCH  score >= 52
HOLD   score >= 45
AVOID  score < 45
```

卖出风险评分单独计算，动作包括：

```text
HOLD / REDUCE / TAKE_PROFIT / STOP_LOSS / NO_POSITION
```

## 开发与测试

运行测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

检查前端脚本语法：

```bash
node --check src/web_static/app.js
```

## 文档索引

- `doc/quantitative_system_architecture.md`：当前架构和数据流。
- `doc/cli_design.md`：CLI 命令和输出。
- `doc/stock_indicators.md`：指标、形态、模型特征和评分。
- `doc/fund_flow_design.md`：资金流数据和评分接入。
- `doc/divergence_signal_design.md`：背驰识别和入场扫描。
- `doc/sell_decision_design.md`：持仓卖出风险判断。
- `doc/price_volume_cognition.md`：量价信号认知边界。

## 风险提示

任何输出都只是辅助分析。交易前仍应检查市场环境、个股公告、仓位比例、流动性、止损线和自身风险承受能力。
