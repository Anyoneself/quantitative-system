# 当前系统架构

本文档描述当前代码已经实现的架构，不描述尚未落地的数据库、实盘交易、组合管理或分布式调度。

## 1. 系统定位

当前系统是本地量价分析和筛选工具，核心目标是：

- 输入股票代码，获取公开日线和资金流数据。
- 计算基础指标、量价形态、机器学习概率、资金流和背驰信号。
- 生成买入观察评分或持仓卖出风险评分。
- 支持股票池扫描、Web 全 A 扫描和单股回测。

系统不负责下单，不连接券商，不做实盘执行。

## 2. 分层结构

```text
CLI / Web 前端
    ↓
应用入口
    src/cli.py
    src/web.py
    ↓
分析服务
    src/analysis/service.py
    ↓
分析模块
    indicators / patterns / ml_model / fund_flow / divergence / advisor / sell_advisor
    ↓
数据模块
    src/data/public_sources.py
    src/data/fund_flow.py
    src/data/source.py
```

回测模块 `src/backtest/` 复用分析模块，但按历史切片生成信号。

## 3. 数据流

单股买入分析：

```text
股票代码
  -> fetch_bars()
  -> calculate_indicators()
  -> detect_patterns()
  -> predict_next_day_buy_probability()
  -> fetch_fund_flow_bars() + analyze_fund_flow()
  -> analyze_divergence()
  -> generate_advice()
  -> CLI 文本 / JSON / Web JSON
```

持仓卖出分析：

```text
股票代码 + 成本价
  -> fetch_bars()
  -> calculate_indicators()
  -> detect_patterns()
  -> analyze_fund_flow()
  -> analyze_divergence()
  -> build_sell_advice()
```

股票池扫描：

```text
股票池
  -> 逐只 analyze_stock()
  -> 过滤最低评分
  -> 按评分和概率排序
  -> 输出 Top N
```

Web 全 A 扫描：

```text
新浪 A 股列表
  -> 过滤创业板、科创板、北交所等代码段
  -> 线程池逐只 analyze_stock()
  -> 实时维护 Top N
  -> SSE 推送扫描快照
```

单股回测：

```text
历史日线
  -> 每个交易日收盘后用历史切片生成信号
  -> 下一交易日开盘成交
  -> 更新现金、持仓、净值和回撤
```

## 4. 核心模块职责

### `src/data/`

- `models.py`：定义 `StockInfo`、`PriceVolumeBar`、`FundFlowBar`。
- `public_sources.py`：抓取腾讯/新浪日线、抓取新浪股票列表、处理限速和重试。
- `fund_flow.py`：抓取东方财富日级资金流，并做进程内缓存。
- `source.py`：对外暴露统一数据读取函数。
- `errors.py`：定义数据刷新异常。

### `src/analysis/`

- `indicators.py`：计算基础指标。
- `patterns.py`：识别量价形态。
- `ml_model.py`：计算机器学习历史达标概率。
- `fund_flow.py`：将资金流转成信号和评分调整。
- `divergence.py`：识别上涨/下跌背驰候选。
- `advisor.py`：生成买入观察评分和解释。
- `sell_advisor.py`：生成持仓卖出风险评分和解释。
- `scanner.py`：股票池扫描和入场扫描。
- `market_scanner.py`：Web 全 A 扫描状态机。
- `service.py`：组合数据读取与分析流程。

### `src/backtest/`

- `engine.py`：单股回测引擎。
- `formatter.py`：回测结果文本和 JSON 输出。

### `src/web.py`

提供本地 HTTP 服务：

- 静态前端文件。
- 单股分析 API。
- 持仓卖出 API。
- 股票池扫描 API。
- 全 A 扫描启动、停止、状态和事件流 API。

## 5. Web API

### `POST /api/advise`

请求：

```json
{"symbol": "600519", "algorithm": "robust_ensemble"}
```

响应包含：

- `advice`
- `chart`
- `fund_flow`
- `divergence`
- `beginner`

### `POST /api/sell-advice`

请求：

```json
{
  "symbol": "600519",
  "cost_price": 1500,
  "quantity": 100,
  "max_loss_rate": 0.08,
  "target_profit_rate": 0.20
}
```

响应包含：

- `sell_advice`
- `fund_flow_risk`
- `divergence_risk`

### `POST /api/scan`

扫描用户传入的股票池，返回一次性结果。

### `POST /api/market-scan/start`

启动全 A 扫描。重复启动会替换上一轮任务。

### `POST /api/market-scan/stop`

请求停止当前全 A 扫描。

### `GET /api/market-scan/status`

返回当前扫描快照。

### `GET /api/market-scan/stream`

通过 SSE 推送扫描快照。

## 6. 数据源与容错

日线行情：

- 优先腾讯接口。
- 失败后使用新浪接口兜底。
- 通过 `PUBLIC_KLINE_SOURCE` 可调整优先顺序。

股票列表：

- 使用新浪公开 A 股列表。
- Web 全 A 扫描逐页读取，读取后立即提交分析任务。

资金流：

- 使用东方财富日级资金流接口。
- 资金流失败时返回 `None`，不阻断主分析。

容错：

- 日线行情有超时、重试和全局限速。
- Web 扫描中单只股票失败只计入失败数，不中断整轮。
- Web 扫描有单任务超时保护。

## 7. 当前边界

- 没有数据库，行情数据不落盘。
- 没有复权处理开关，日线以公开接口返回为准。
- 没有指数择时、行业中性、组合仓位管理。
- 没有实盘交易、模拟盘订单、风控前置网关。
- 没有幸存者偏差处理，全 A 扫描只扫描当前公开列表。
- 回测是单股规则验证，不等同于组合策略评估。

## 8. 可优先改进方向

按当前阶段，优先级建议：

1. 为行情解析和资金流解析补更多稳定测试。
2. 将回测结果导出为 CSV 或 JSON 文件，便于复盘。
3. 给入场扫描增加 Web 页面入口。
4. 增加数据质量检查，例如缺失、重复日期、异常成交额。
5. 对资金流失败增加有限重试和过期缓存。
