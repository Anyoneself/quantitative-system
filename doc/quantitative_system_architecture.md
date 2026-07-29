# 当前系统架构

本文档描述仓库当前已经实现的模块、数据流、API、SQLite 表和部署方式。规划中的自动交易、多用户权限和分布式任务不属于当前架构。

## 1. 系统定位

系统是本地 A 股量化研究、筛选和组合复盘工具，主要职责：

- 从免登录公开接口获取日线、股票列表和日级资金流。
- 保存原始 HTTP 响应并校验标准化日线质量。
- 计算指标、量价形态、模型分数、资金流和背驰信号。
- 提供时间顺序模型验证和单股策略回测。
- 使用 SQLite 管理组合、交易流水、持仓和净值。
- 计算组合收益、风险和资产配置。
- 通过 CLI、JSON API、SSE 和白色 Web 前端提供功能。
- 通过 Docker/Compose 运行并持久化本地数据。

系统不连接券商账户，不生成或下达真实委托。

## 2. 技术选择

- Python 3.11。
- Python 标准库 HTTP 服务和 `sqlite3`。
- 原生 HTML、CSS、JavaScript，无前端构建链。
- SQLite 单文件数据库，开启外键、WAL 和 busy timeout。
- 本地文件保存公开接口原始响应。
- SSE 推送 Web 全市场扫描进度。
- 单应用进程；没有 Redis、消息队列、微服务或外部数据库。

## 3. 实际目录

```text
src/
├── analysis/
│   ├── advisor.py
│   ├── divergence.py
│   ├── evaluation.py
│   ├── fund_flow.py
│   ├── indicators.py
│   ├── market_scanner.py
│   ├── ml_model.py
│   ├── patterns.py
│   ├── scanner.py
│   ├── sell_advisor.py
│   └── service.py
├── api/
│   ├── portfolio.py
│   └── research.py
├── backtest/
│   ├── engine.py
│   └── formatter.py
├── data/
│   ├── errors.py
│   ├── fund_flow.py
│   ├── market_rules.py
│   ├── models.py
│   ├── public_sources.py
│   ├── quality.py
│   └── source.py
├── output/
│   └── formatter.py
├── portfolio/
│   ├── performance.py
│   ├── risk.py
│   └── service.py
├── storage/
│   ├── connection.py
│   ├── market_repository.py
│   ├── migrations.py
│   ├── models.py
│   └── portfolio_repository.py
├── web_static/
│   ├── app.js
│   ├── index.html
│   └── styles.css
├── cli.py
└── web.py
```

## 4. 分层与职责

### `src/data/`

- `models.py`：公开数据传输对象。
- `public_sources.py`：腾讯前复权分析日线、腾讯/新浪不复权估值日线、股票列表、超时、重试、限速和原始响应落盘。
- `fund_flow.py`：东方财富日级资金流和进程内缓存。
- `quality.py`：日期顺序、重复日期、OHLC、有限数值和成交量校验。
- `market_rules.py`：根据代码、名称和交易日期近似计算涨跌停价。
- `source.py`：统一数据读取入口。
- `errors.py`：数据刷新与质量异常。

### `src/analysis/`

- `indicators.py`：均线、收益、成交量、MACD、位置等指标。
- `patterns.py`：突破、缩量回调、K 线、MACD、年线等形态。
- `ml_model.py`：KNN、加权 KNN、时间衰减 KNN、逻辑回归和稳健集成。
- `evaluation.py`：时间顺序 walk-forward、验证段阈值选择和测试段指标。
- `fund_flow.py`：将资金流数据转成信号和评分调整。
- `divergence.py`：价格与 MACD、量能、资金流的背驰候选。
- `advisor.py`：买入观察评分、证据和风险。
- `sell_advisor.py`：持仓卖出风险。
- `scanner.py`：股票池与入场扫描。
- `market_scanner.py`：Web 全市场扫描状态机和线程池。
- `service.py`：组合公开数据读取与分析流程。

### `src/backtest/`

- `engine.py`：历史切片、次日成交、费用、滑点、涨跌停、期末平仓、完整交易和净值指标。
- `formatter.py`：CLI 文本/JSON 格式。

### `src/storage/`

- `connection.py`：数据库路径、父目录创建、外键、WAL 和 5 秒 busy timeout。
- `migrations.py`：版本化、幂等迁移。
- `models.py`：数据库记录对象。
- `market_repository.py`：证券和日线读写。
- `portfolio_repository.py`：组合、流水、持仓和净值读写。

### `src/portfolio/`

- `service.py`：创建组合、录入交易、校验现金/持仓、重放流水和重建持仓。
- `performance.py`：按日期重建现金、市值、总资产、净值、日收益和回撤。
- `risk.py`：年化波动率、Sharpe、Beta、历史 VaR、完整持仓周期胜率、盈亏比和风险等级。

### `src/api/`

- `portfolio.py`：组合、交易、行情刷新、仪表盘和风险响应。
- `research.py`：walk-forward 与回测请求校验和响应。

### `src/web.py`

- 静态文件服务。
- API 路由和 JSON 参数处理。
- SSE 全市场扫描推送。
- 启动时自动执行数据库迁移。

## 5. 主要数据流

### 5.1 公开行情

```text
股票代码
  -> 分析/回测：腾讯前复权日线
  -> 组合估值：腾讯或新浪不复权日线
  -> 原始响应写入 PUBLIC_DATA_RAW_DIR
  -> JSON 解析
  -> PriceVolumeBar
  -> validate_price_bars()
  -> 分析或标准化入库
```

原始文件包含来源、URL、抓取时间、内容 SHA-256 和响应正文，默认目录：

```text
data/raw/http/{source}/{YYYYMMDD}/
```

### 5.2 单股买入分析

```text
fetch_bars()
  -> calculate_indicators()
  -> detect_patterns()
  -> predict_next_day_buy_probability()
  -> fetch_fund_flow_bars() + analyze_fund_flow()
  -> analyze_divergence()
  -> generate_advice()
  -> CLI 或 Web JSON
```

### 5.3 模型验证

```text
按时间推进信号日
  -> 只传入信号日及以前的历史
  -> 重新训练并预测
  -> 未来固定持有期生成事后标签
  -> 按预测周期抽样 OOS 点
  -> 时间顺序切分验证段和测试段
  -> 验证段选择阈值
  -> 测试段使用冻结阈值
```

标签从次日开盘含滑点买入开始，固定周期末收盘含滑点卖出，并扣除双边比例佣金和卖出印花税。详细口径见 [模型验证口径与风险边界](model_validation_and_risk.md)。

### 5.4 单股回测

```text
历史日线
  -> 每日收盘后用历史切片生成买卖信号
  -> 下一交易日开盘尝试成交
  -> 检查成交量、价格区间和涨跌停
  -> 计入佣金、最低佣金、印花税和滑点
  -> 更新现金、持仓、完整交易和净值
  -> 默认期末平仓
```

回测不会向历史信号传入未来 K 线。日线数据仍无法模拟盘口队列、部分成交和盘中路径。

### 5.5 组合与净值

```text
创建组合
  -> 录入带 request_id 的 BUY/SELL
  -> 校验同一组合内 request_id 幂等
  -> 刷新并校验交易日期对应的 NONE 未复权日线
  -> 首次请求写入 transactions；重复请求返回已有流水
  -> 按日期和 ID 重放全部流水
  -> 校验现金与持仓
  -> 替换 positions
  -> 刷新组合证券和基准的 NONE 未复权日线
  -> 重建 portfolio_equity
  -> 校验净值日期是否覆盖当前账本
  -> 计算风险与仪表盘
```

组合估值只允许使用 `daily_bars.adjustment_type = "NONE"` 的未复权行情，避免把研究使用的前复权价格与用户成交价混合。交易日期必须精确命中该证券已经保存并通过质量校验的未复权日线；周末检查只能提前拒绝明显错误，不能代替交易日存在性校验。

组合净值缺少某一持仓当日行情时沿用最近已知的未复权收盘价，不读取未来价格；完全没有历史价格时拒绝计算该日期净值。若最近净值日期落后于当前账本可计算日期，仪表盘风险状态返回 `STALE`；没有可用净值时返回 `NO_DATA`，两者均不把旧指标或 `0` 当成当前风险。

### 5.6 全市场扫描

```text
新浪当前 A 股列表
  -> 过滤不在当前扫描范围的代码段
  -> 线程池逐只分析
  -> 维护 Top N
  -> 状态 API / SSE
```

当前列表不是历史证券全集，因此不能用于消除历史回测的幸存者偏差。

## 6. SQLite

### 6.1 路径和连接

默认数据库：

```text
data/app/quantitative_system.db
```

可通过 `QUANT_DB_PATH` 覆盖。连接配置：

- `PRAGMA foreign_keys = ON`
- `PRAGMA journal_mode = WAL`
- `PRAGMA busy_timeout = 5000`

数据库在 Web 启动、健康检查和存储服务调用时执行幂等初始化。

### 6.2 当前 7 张表

| 表 | 主键 | 用途 |
|---|---|---|
| `schema_migrations` | `version` | 迁移版本和执行时间 |
| `instruments` | `symbol` | 名称、市场、类型、上市/退市日期 |
| `daily_bars` | `(symbol, trade_date)` | 整数分 OHLC、成交量、成交额、复权标识、来源 |
| `portfolios` | `id` | 名称、初始资金、比较基准、起始日 |
| `transactions` | `id` | 幂等请求号、BUY/SELL、价格、数量、佣金、印花税 |
| `positions` | `(portfolio_id, symbol)` | 流水重放后的数量和成本 |
| `portfolio_equity` | `(portfolio_id, trade_date)` | 现金、市值、总资产、净值、日收益、回撤、流水版本和行情指纹 |

关键约束：

- 日线依赖 `instruments` 外键，OHLC 必须为正且高低价关系有效。
- 交易和持仓依赖组合和证券外键。
- 删除组合会级联删除其流水、持仓和净值。
- 交易方向只允许 `BUY`、`SELL`。
- `transactions` 在同一 `portfolio_id` 内对 `request_id` 建立唯一约束；重复请求返回原流水，不重复记账。
- 组合估值查询必须显式限定 `daily_bars.adjustment_type = "NONE"`。
- 净值保存交易最大 ID 作为流水版本，并保存所用未复权行情内容指纹；任一版本不一致时仪表盘返回 `STALE`，不展示旧风险。
- 金额和价格使用整数分；净值、日收益和回撤按明确小数字符串保存。
- API 单项金额或价格、累计现金、持仓成本、市值和总资产均不超过 `10^12` 元，数量不超过 `10^9` 股；任何单笔成交金额也必须落在同一账本上限内。
- 当前没有策略表、回测表或模型评估表。

## 7. 风险服务

组合风险输入：

- `portfolio_equity.daily_return`
- 比较基准的对齐日收益
- 流水重放得到的完整持仓周期盈亏
- 净值序列最大回撤

输出：

| 指标 | 口径 |
|---|---|
| 年化波动率 | 日收益样本标准差 × `sqrt(252)` |
| Sharpe | 年化平均日收益减无风险利率后除以年化波动率；默认无风险利率为 0 |
| Beta | 组合与基准日收益协方差 / 基准方差 |
| VaR(95%) | 历史日收益 5% 分位损失 |
| 完整持仓周期胜率 | 同一证券从首次买入到持仓归零形成一个周期，盈利周期占完整周期的比例 |
| 盈亏比 | 平均盈利 / 平均亏损绝对值 |

波动率、Sharpe、Beta 和 VaR 至少需要 20 个有效日收益样本。指标无法计算时返回 `None` 和具体原因。

风险响应先检查净值新鲜度：

- `STALE`：存在历史净值，但净值日期落后于当前交易账本可计算日期。历史序列可以用于复盘，但不能标记为当前风险。
- `NO_DATA`：没有可用净值，或有效样本不足以形成当前风险判断。
- 只有净值覆盖当前账本时，才按下表阈值给出正常风险等级。

风险等级阈值：

| 维度 | 需要关注 | 高风险 |
|---|---:|---:|
| 年化波动率 | 30% | 45% |
| 历史 VaR(95%) | 3% | 5% |
| 最大回撤绝对值 | 15% | 25% |

这些阈值只用于提示复核风险敞口，不触发自动交易。

## 8. Web 前端

前端为原生 HTML/CSS/JavaScript。页面组成：

- 总览：组合摘要、净值、基准、资产配置、风险和策略入口。
- 行情中心：单股分析、卖出判断、K 线和扫描。
- 策略研究：walk-forward 测试指标。
- 策略回测：回测参数、风险收益指标和成交记录。
- 投资组合：创建组合、录入交易、持仓和流水。

侧栏风险入口定位到总览风险面板。数据不足时展示空状态，不硬编码收益指标。

## 9. HTTP API

### 9.1 健康、仪表盘与组合

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 初始化并检查数据库 |
| `GET` | `/api/dashboard?portfolio_id={id}` | 指定或首个组合的聚合仪表盘 |
| `GET` | `/api/portfolios` | 组合列表 |
| `POST` | `/api/portfolios` | 创建组合 |
| `GET` | `/api/portfolios/{id}` | 组合详情、持仓、流水和净值 |
| `POST` | `/api/portfolios/{id}/transactions` | 录入交易并重放组合 |
| `POST` | `/api/portfolios/{id}/refresh` | 刷新组合行情与基准，重建净值 |

当前不提供修改、删除组合或删除交易的 API。

交易写入请求必须包含调用方生成的 `request_id`。幂等范围是单个组合：同一组合重复提交相同 `request_id` 返回已存在的交易，不会再次扣减现金或增加持仓。写入前还必须同时满足：

- `trade_date` 精确存在于该证券已保存且通过校验的 `NONE` 未复权日线。
- 单项价格、佣金、印花税等金额不超过 `10^12` 元。
- 数量不超过 `10^9` 股，价格与数量乘积换算为分后处于 SQLite 安全整数范围内。

这些校验是账本一致性和存储安全约束，不代表交易可在真实市场成交。

### 9.2 研究与回测

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/evaluate` | walk-forward 验证 |
| `POST` | `/api/backtest` | 单股策略回测 |

`/api/evaluate` 请求：

```json
{
  "symbol": "600519",
  "algorithm": "robust_ensemble"
}
```

`/api/backtest` 可接受：

```json
{
  "symbol": "600519",
  "algorithm": "robust_ensemble",
  "initial_cash": 100000,
  "buy_score": 60,
  "sell_risk": 45,
  "max_position_rate": 0.95,
  "commission_rate": 0.0003,
  "min_commission": 5,
  "stamp_duty_rate": 0.0005,
  "slippage_rate": 0.0005,
  "liquidate_at_end": true
}
```

### 9.3 行情、建议和扫描

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/advise` | 单股买入观察 |
| `POST` | `/api/sell-advice` | 持仓卖出风险 |
| `POST` | `/api/scan` | 一次性股票池扫描 |
| `POST` | `/api/market-scan/start` | 启动全市场单轮扫描 |
| `POST` | `/api/market-scan/stop` | 请求停止扫描 |
| `GET` | `/api/market-scan/status` | 当前扫描快照 |
| `GET` | `/api/market-scan/stream` | SSE 扫描快照 |

## 10. 运行与部署

### 本地

```bash
./scripts/quant web --port 8000
```

启动顺序：

```text
设置 PYTHONPATH
  -> 初始化 SQLite
  -> 绑定端口
  -> 提供静态页面和 API
```

### Docker

Dockerfile：

- 基于 `python:3.11-slim`。
- 设置 `/app/src` 为 `PYTHONPATH`。
- 以 `quant` 非 root 用户运行。
- 暴露 `8000`。
- 使用 `/api/health` 健康检查。
- 启动前执行幂等迁移。

Compose：

- 单个 `quant` 服务。
- 宿主监听地址和端口由 `QUANT_WEB_BIND`、`QUANT_WEB_PORT` 控制，默认只监听 `127.0.0.1`。
- `quant-data` 卷挂载到 `/app/data`。
- 容器内数据库为 `/app/data/app/quantitative_system.db`。
- 容器内原始响应目录为 `/app/data/raw/http`。

## 11. 当前边界

- 没有自动交易、券商连接、订单路由或实盘风控网关。
- 没有多用户认证、授权和审计日志。
- 没有数据库中的策略定义、模型版本、回测任务或评估结果持久化。
- 没有历史完整证券主表、指数成分、ST 状态和财务点时库。
- 模型与回测固定使用腾讯前复权序列；组合估值固定使用不复权序列，不跨口径兜底。
- 组合账本尚未自动处理分红、送转和拆并股，公司行动仍需人工复核。
- 全市场扫描使用当前公开股票列表，存在幸存者偏差。
- 日线回测无法完整模拟盘口流动性和盘中成交路径。
- 风险等级是提示，不会自动改变持仓。

## 12. 自动交易与监管

当前系统只输出研究结果，不执行交易。

未来若接入券商接口并通过程序自动生成或下达委托，属于实盘程序化交易能力扩展，应另行建设账户权限、订单状态机、前置风控、幂等、防重复下单、紧急停止、审计和故障恢复，并在上线前确认适用监管要求。

现行公开规则包括：

- [中国证监会《证券市场程序化交易管理规定（试行）》](https://www.csrc.gov.cn/csrc/c100028/c7480577/content.shtml)。
- [上海证券交易所程序化交易管理实施细则](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20250612_10781696.shtml)。
- [深圳证券交易所程序化交易管理实施细则](https://www.szse.cn/lawrules/rule/allrules/bussiness/t20250403_612770.html)。
- [上海证券交易所交易规则（2026 年修订）](https://big5.sse.com.cn/site/cht/www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)。
- [深圳证券交易所交易规则（2026 年修订）](https://www.szse.cn/lawrules/rule/trade/current/t20260424_620190.html)。

实际接入前应向券商和专业合规人员确认报告、监控、系统安全和交易行为要求。本文档不构成法律意见。
