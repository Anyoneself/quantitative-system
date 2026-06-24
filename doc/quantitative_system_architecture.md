# Python 量化系统基本架构设计

## 1. 目标与原则

本系统目标是构建一个可持续迭代的股票量化研究与交易平台，覆盖数据获取、数据治理、因子研究、策略回测、组合管理、风险控制、模拟交易、实盘交易与监控复盘。

需要明确的是，量化系统不能保证持续盈利。可持续盈利来自长期的数据质量、策略有效性、交易成本控制、风控纪律、监控告警和持续复盘。本架构的核心目标是让每一个交易决策都可以被验证、回放、解释和改进。

设计原则：

- Python 优先：研究、回测、交易、监控均以 Python 生态为主。
- 模块解耦：数据、策略、回测、交易、风控、监控各自独立，通过清晰接口协作。
- 先研究后实盘：任何策略必须经过样本内研究、样本外验证、模拟交易和小资金验证。
- 防止过拟合：严格区分训练集、验证集、测试集，记录每次实验参数和结果。
- 风控前置：交易信号必须经过组合约束、仓位控制、止损规则和异常检查。
- 可审计：保存原始数据、处理后数据、策略版本、订单、成交、持仓和每日净值。

## 2. 总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│                         用户层                               │
│  CLI / Notebook / Web Dashboard / Report                    │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                       应用服务层                             │
│  研究服务 / 回测服务 / 模拟交易 / 实盘交易 / 监控告警          │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                       核心引擎层                             │
│  Data Engine / Factor Engine / Strategy Engine              │
│  Backtest Engine / Portfolio Engine / Risk Engine           │
│  Execution Engine / Accounting Engine                       │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                       基础设施层                             │
│  数据库 / 文件存储 / 消息队列 / 调度器 / 日志 / 配置 / 密钥     │
└─────────────────────────────────────────────────────────────┘
```

核心数据流：

```text
数据源
  -> 原始数据落库
  -> 数据清洗与复权
  -> 特征与因子计算
  -> 策略生成目标仓位
  -> 风控检查
  -> 订单生成
  -> 模拟或实盘执行
  -> 成交与持仓更新
  -> 绩效归因与复盘
```

## 3. 推荐项目目录

建议将项目逐步演进为以下结构：

```text
quantitative-system/
├── README.md
├── pyproject.toml
├── .env.example
├── config/
│   ├── base.yaml
│   ├── dev.yaml
│   ├── paper.yaml
│   └── live.yaml
├── doc/
│   └── quantitative_system_architecture.md
├── notebooks/
│   ├── factor_research/
│   └── strategy_research/
├── scripts/
│   ├── ingest_daily.py
│   ├── advise_stock.py
│   ├── run_backtest.py
│   ├── run_paper_trading.py
│   └── run_live_trading.py
├── src/
│   └── quant_system/
│       ├── __init__.py
│       ├── common/
│       │   ├── config.py
│       │   ├── logging.py
│       │   ├── calendar.py
│       │   └── exceptions.py
│       ├── data/
│       │   ├── sources/
│       │   │   ├── public_sources.py
│       │   │   ├── public_client.py
│       │   │   └── public_parser.py
│       │   ├── crawler/
│       │   │   ├── scheduler.py
│       │   │   ├── rate_limiter.py
│       │   │   └── retry.py
│       │   ├── schemas.py
│       │   ├── storage.py
│       │   ├── cleaner.py
│       │   └── loader.py
│       ├── factors/
│       │   ├── base.py
│       │   ├── technical.py
│       │   ├── fundamental.py
│       │   └── registry.py
│       ├── strategies/
│       │   ├── base.py
│       │   ├── momentum.py
│       │   ├── mean_reversion.py
│       │   └── multi_factor.py
│       ├── portfolio/
│       │   ├── optimizer.py
│       │   ├── position_sizer.py
│       │   └── rebalancer.py
│       ├── risk/
│       │   ├── limits.py
│       │   ├── checks.py
│       │   └── stop_loss.py
│       ├── backtest/
│       │   ├── engine.py
│       │   ├── broker.py
│       │   ├── slippage.py
│       │   ├── commission.py
│       │   └── metrics.py
│       ├── execution/
│       │   ├── broker_api.py
│       │   ├── order_manager.py
│       │   └── paper_broker.py
│       ├── accounting/
│       │   ├── account.py
│       │   ├── position.py
│       │   └── pnl.py
│       ├── monitoring/
│       │   ├── alerts.py
│       │   ├── healthcheck.py
│       │   └── reports.py
│       └── experiment/
│           ├── tracker.py
│           └── artifacts.py
└── tests/
    ├── unit/
    ├── integration/
    └── backtest/
```

## 4. 模块设计

### 4.1 数据模块

职责：

- 从公开行情采集行情、财务、指数成分、交易日历、停复牌、涨跌停、分红送转等数据。
- 保存原始数据，避免数据源变更后无法回放。
- 统一清洗、复权、对齐交易日和处理缺失值。
- 为研究、回测和实盘提供一致的数据读取接口。

公开行情数据采集设计：

- 采集入口：将公开行情作为第一数据源，统一封装在 `src/quant_system/data/sources/public_sources.py`。
- 请求客户端：`public_client.py` 负责请求头、会话、超时、代理配置、限速和重试。
- 解析器：`public_parser.py` 只负责把 HTML、JSON 或接口响应解析成标准字段。
- 任务调度：`crawler/scheduler.py` 负责日线、基础信息、财务数据等不同任务的定时采集。
- 限速控制：`crawler/rate_limiter.py` 控制请求频率，避免高频访问导致 IP 或账号被限制。
- 失败重试：`crawler/retry.py` 对网络错误、临时空数据、服务端限流做有限次数重试。
- 原始留痕：每次采集先保存原始响应或原始解析结果，再进入清洗流程。
- 数据版本：采集日期、数据源页面或接口、请求参数、解析版本都要入库，方便回溯。
- 合规边界：优先使用公开可访问页面或正式授权接口；遵守公开行情网站服务条款、robots 规则和适用法律，不绕过登录、付费、验证码、加密、访问控制或反爬限制。

建议公开行情采集源接口：

```python
from abc import ABC, abstractmethod
from datetime import date
import pandas as pd


class MarketDataSource(ABC):
    @abstractmethod
    def fetch_daily_bars(self, trade_date: date) -> pd.DataFrame:
        """Fetch daily OHLCV data for all available symbols."""

    @abstractmethod
    def fetch_stock_basic(self) -> pd.DataFrame:
        """Fetch stock metadata such as symbol, name, exchange, industry."""

    @abstractmethod
    def fetch_financials(self, symbol: str) -> pd.DataFrame:
        """Fetch financial statement or financial indicator data."""


class PublicDataSource(MarketDataSource):
    def fetch_daily_bars(self, trade_date: date) -> pd.DataFrame:
        raise NotImplementedError

    def fetch_stock_basic(self) -> pd.DataFrame:
        raise NotImplementedError

    def fetch_financials(self, symbol: str) -> pd.DataFrame:
        raise NotImplementedError
```

推荐数据类型：

- 日线行情：开盘价、最高价、最低价、收盘价、成交量、成交额、换手率。
- 分钟行情：用于更细粒度执行和滑点估计，初期可以暂缓。
- 财务数据：利润表、资产负债表、现金流量表、估值指标。
- 股票基础信息：上市日期、退市日期、行业、市值、指数成分。
- 交易状态：停牌、涨跌停、ST 状态、可交易标记。

推荐存储：

- 原始文件：`data/raw/public/`，使用 JSON、HTML、CSV 或 Parquet，保留采集原貌。
- 处理后数据：`data/processed/`，优先 Parquet。
- 元数据和交易记录：PostgreSQL 或 SQLite。
- 高频或大量行情：ClickHouse、DuckDB 或 Parquet 分区。

关键要求：

- 回测不能使用未来数据。
- 财务数据必须按公告日期生效，而不是报告期日期。
- 指数成分和股票池必须使用历史成分，避免幸存者偏差。
- 爬虫解析字段必须有单元测试，防止页面结构变化后静默产生脏数据。
- 公开行情数据采集任务必须有频率限制、失败告警和数据完整性校验。

### 4.2 因子模块

职责：

- 初期重点计算价格和成交量相关因子，后续再扩展基本面、风险、情绪等因子。
- 对因子做标准化、去极值、中性化、缺失处理。
- 评估因子的 IC、Rank IC、分组收益、换手率和稳定性。

基础因子方向：

- 动量：过去 N 日收益率、突破、均线趋势。
- 反转：短期超跌、偏离均线、成交异常。
- 波动：历史波动率、下行波动率、ATR。
- 量价：量能变化、换手率、价量背离。
- 涨停：涨停标记、缩量涨停、放量炸板、涨停后承接。
- 质量：ROE、毛利率、现金流质量。
- 估值：PE、PB、PS、股息率。
- 规模：总市值、流通市值。

因子接口示例：

```python
from abc import ABC, abstractmethod
import pandas as pd


class Factor(ABC):
    name: str

    @abstractmethod
    def compute(self, data: pd.DataFrame) -> pd.Series:
        """Return factor values indexed by date and symbol."""
```

### 4.3 策略模块

职责：

- 根据行情、因子、持仓和风险约束生成目标仓位。
- 输出标准化信号，而不是直接下单。
- 支持单策略、多策略组合和策略权重分配。

策略接口示例：

```python
from abc import ABC, abstractmethod
import pandas as pd


class Strategy(ABC):
    name: str

    @abstractmethod
    def generate_targets(
        self,
        market_data: pd.DataFrame,
        factors: pd.DataFrame,
        positions: pd.DataFrame,
    ) -> pd.DataFrame:
        """Return target weights or target shares by symbol."""
```

初期建议实现三类策略：

- 趋势跟踪：只在中长期趋势向上时持有。
- 均值回归：选择短期过度下跌但流动性良好的股票。
- 多因子选股：综合质量、估值、动量、波动和规模因子打分。

### 4.4 回测模块

职责：

- 模拟真实交易过程，包括调仓、成交、费用、滑点、停牌、涨跌停、资金占用。
- 输出净值、收益、回撤、波动、夏普、胜率、换手、暴露和归因。
- 支持事件驱动和向量化回测，初期可优先日频向量化。

回测必须包含：

- 手续费和印花税。
- 滑点模型。
- 涨跌停不可买卖限制。
- 停牌不可交易限制。
- 最小交易单位。
- 调仓延迟，例如 T 日收盘信号，T+1 开盘或收盘成交。
- 现金和持仓账户。

关键指标：

- 年化收益率。
- 最大回撤。
- 夏普比率。
- 卡玛比率。
- 波动率。
- 胜率和盈亏比。
- 月度收益。
- 超额收益和信息比率。
- 换手率和交易成本占比。

### 4.5 组合与仓位模块

职责：

- 把策略信号转换成可执行的目标仓位。
- 控制单票、行业、风格、整体杠杆和现金比例。
- 降低换手率，避免频繁交易吞噬收益。

常用约束：

- 单只股票最大权重，例如 5%。
- 单行业最大权重，例如 25%。
- 最小持仓数量，例如 20 只。
- 最大持仓数量，例如 80 只。
- 单日最大换手率。
- 最低成交额过滤。
- ST、停牌、上市未满 N 日股票过滤。

### 4.6 风控模块

职责：

- 在下单前检查风险。
- 在交易中处理异常。
- 在交易后评估风险暴露。

基础风控规则：

- 最大总仓位限制。
- 最大单票仓位限制。
- 最大行业暴露限制。
- 最大单日亏损限制。
- 最大回撤触发降仓。
- 股票流动性检查。
- 异常价格检查。
- 连续数据缺失检查。
- 订单金额和数量上限。

强烈建议设置熔断规则：

- 当日亏损超过阈值，停止新开仓。
- 净值回撤超过阈值，降低总仓位。
- 数据源异常或行情延迟，停止自动交易。
- 订单连续失败，切换人工确认。

### 4.7 交易执行模块

职责：

- 接收目标仓位，生成订单。
- 调用券商或交易接口执行。
- 管理订单状态、成交回报、撤单和重试。
- 支持模拟交易与实盘交易共用同一套上层接口。

核心对象：

- `Order`：订单。
- `Trade`：成交。
- `Position`：持仓。
- `Account`：账户。
- `Broker`：券商接口抽象。

执行流程：

```text
目标仓位
  -> 当前持仓对比
  -> 生成交易差额
  -> 风控检查
  -> 订单切片
  -> 发送订单
  -> 更新成交
  -> 更新账户和持仓
```

### 4.8 监控与复盘模块

职责：

- 监控数据任务、策略任务、订单、成交、持仓、净值和风险。
- 生成日报、周报、月报。
- 记录异常并告警。

监控内容：

- 数据是否按时更新。
- 最新交易日是否完整。
- 策略是否按时产生信号。
- 实盘持仓是否偏离目标仓位。
- 订单是否全部成交。
- 净值、回撤、仓位、行业暴露是否异常。
- 策略表现是否低于预期。

可选告警渠道：

- 日志文件。
- 邮件。
- 企业微信或飞书。
- Web Dashboard。

## 5. 技术选型

推荐 Python 生态：

- 公开行情采集：`httpx` 或 `requests`、`beautifulsoup4`、`lxml`、`tenacity`。
- 动态页面采集：必要时使用 `playwright`，但只用于公开可访问页面的正常浏览式采集。
- 数据处理：`pandas`、`numpy`、`polars`。
- 本地分析：`duckdb`、`pyarrow`、`parquet`。
- 数据库：`sqlite` 起步，后续升级 `postgresql`。
- 回测：初期自研轻量回测核心，或参考 `backtrader`、`vectorbt` 的设计。
- 统计分析：`scipy`、`statsmodels`。
- 机器学习：`scikit-learn`、`lightgbm`。
- 可视化：`matplotlib`、`plotly`。
- 配置：`pydantic-settings`、`pyyaml`。
- 调度：`apscheduler` 起步，后续可用 `airflow` 或 `prefect`。
- API 服务：`fastapi`。
- 测试：`pytest`。
- 代码质量：`ruff`、`mypy`。

## 6. 环境划分

建议至少保留四套环境：

- `dev`：本地开发和单元测试。
- `research`：Notebook 与策略研究。
- `paper`：模拟交易，连接真实行情但不真实下单。
- `live`：实盘交易，必须启用严格风控和人工兜底。

不同环境使用独立配置文件：

```text
config/base.yaml
config/dev.yaml
config/paper.yaml
config/live.yaml
```

当前阶段只做公开行情单股分析，不需要必需环境变量。可选环境变量：

```text
PUBLIC_DATA_USER_AGENT=
PUBLIC_DATA_TIMEOUT_SECONDS=
PUBLIC_KLINE_DAYS=
MARKET_SCAN_WORKERS=
MARKET_TASK_TIMEOUT_SECONDS=
```

说明：

- `PUBLIC_DATA_USER_AGENT`：请求使用的浏览器标识，不配置时使用系统默认值。
- `PUBLIC_DATA_TIMEOUT_SECONDS`：公开行情请求超时时间，不配置时使用代码默认值。
- `PUBLIC_KLINE_DAYS`：公开日线接口拉取的历史交易日数量，不配置时使用代码默认值。
- `MARKET_SCAN_WORKERS`：全 A 扫描并发线程数，不配置时使用代码默认值。
- `MARKET_TASK_TIMEOUT_SECONDS`：单只股票分析任务最大等待时间，不配置时使用代码默认值。

券商 API、实盘账户、第三方数据商 Token 等变量暂不加入第一版。等系统进入模拟交易或实盘交易阶段，再按真实需求补充。

## 7. 数据库核心表

初期可以使用 SQLite 或 PostgreSQL 建以下表：

```text
market_daily
- trade_date
- symbol
- source
- open
- high
- low
- close
- volume
- amount
- adj_factor
- is_tradable

stock_basic
- symbol
- source
- name
- exchange
- list_date
- delist_date
- industry

factor_values
- trade_date
- symbol
- factor_name
- factor_value

strategy_signal
- trade_date
- strategy_name
- symbol
- score
- target_weight

orders
- order_id
- account_id
- strategy_name
- symbol
- side
- quantity
- price
- status
- created_at

trades
- trade_id
- order_id
- symbol
- side
- quantity
- price
- commission
- traded_at

positions
- account_id
- trade_date
- symbol
- quantity
- market_value
- weight

portfolio_nav
- account_id
- trade_date
- total_asset
- cash
- market_value
- daily_return
- drawdown

crawl_jobs
- job_id
- source
- job_type
- target_date
- status
- started_at
- finished_at
- error_message

raw_crawl_records
- record_id
- source
- endpoint
- request_params
- response_hash
- raw_storage_path
- crawled_at

data_quality_checks
- check_id
- source
- dataset
- trade_date
- check_name
- status
- details
- checked_at
```

## 8. 最小可行版本

第一阶段不要急着实盘，建议先完成 MVP：

1. 公开行情采集：先完成日线行情、股票基础信息、交易日历的公开数据采集。
2. 原始数据存储：保存公开行情原始响应和标准化后的 Parquet 数据。
3. 数据质量检查：检查字段缺失、重复记录、成交量异常、交易日缺口。
4. 股票池过滤：排除 ST、停牌、上市时间太短、成交额太低股票。
5. 因子计算：实现动量、波动率、换手率、市值等基础因子。
6. 策略样例：实现一个多因子月度调仓策略。
7. 回测引擎：支持费用、滑点、停牌、涨跌停、调仓周期。
8. 绩效报告：输出净值曲线、回撤、年度收益、月度收益、持仓和交易明细。
9. 单元测试：覆盖爬虫解析、数据清洗、因子计算和回测核心逻辑。

MVP 完成标准：

- 能从原始数据生成处理后数据。
- 能稳定完成一次公开行情日线数据采集和质量校验。
- 能运行一个完整回测。
- 能输出可复盘的交易明细和绩效报告。
- 能重复运行并得到一致结果。

## 9. 演进路线

### 阶段一：研究与回测

- 建立数据层和回测层。
- 实现基础因子和多因子策略。
- 完成交易成本、滑点和涨跌停模拟。
- 建立策略实验记录。

### 阶段二：模拟交易

- 接入实时或准实时行情。
- 每日自动生成目标仓位。
- 使用模拟账户执行订单。
- 比较模拟成交与回测假设差异。
- 完善告警和日报。

### 阶段三：小资金实盘

- 接入券商接口。
- 限制单笔订单、总仓位和最大亏损。
- 所有新策略先小资金运行。
- 每日复盘实盘偏差。

### 阶段四：组合化与自动化

- 多策略组合。
- 策略动态权重。
- 风格和行业暴露控制。
- 自动报告和自动异常处理。
- 更完整的实验管理和模型版本管理。

## 10. 关键风险

必须重点防范：

- 未来函数：使用了回测当时不可获得的数据。
- 幸存者偏差：只使用当前仍上市股票。
- 过拟合：策略在历史上表现好，但没有泛化能力。
- 交易成本低估：忽略佣金、印花税、滑点、冲击成本。
- 流动性不足：回测可以买入，实盘买不到或卖不掉。
- 数据错误：复权、停牌、涨跌停、财务公告日期错误。
- 公开行情页面变化：字段位置或接口返回格式变化，导致解析失败或字段错位。
- 爬虫限流：访问频率过高导致请求失败、IP 限制或账号风险。
- 合规风险：未经授权采集受限制数据，或绕过验证码、登录、付费和访问控制。
- 实盘异常：接口超时、重复下单、订单状态不同步。
- 心理和流程风险：连续亏损后随意改策略或放弃风控。

## 11. 推荐开发顺序

建议按以下顺序开发：

1. 初始化 Python 项目结构和配置系统。
2. 实现公开行情采集客户端、限速器、重试器和原始数据落盘。
3. 实现交易日历与数据 schema。
4. 实现日线数据读取、清洗、存储。
5. 实现公开行情解析器单元测试和数据质量检查。
6. 实现股票池过滤器。
7. 实现基础因子库。
8. 实现策略接口和一个多因子策略。
9. 实现日频回测引擎。
10. 实现绩效指标和报告。
11. 增加模拟交易账户。
12. 增加监控告警。
13. 接入券商接口并进入小资金实盘。

## 12. 初始策略建议

建议从低复杂度、可解释、低换手策略开始：

- 月度调仓。
- 股票池排除不可交易和低流动性股票。
- 初期主要使用股票价格和成交量因子。
- 重点观察涨停缩量、放量突破、缩量回调、放量下跌、价涨量缩、价跌量增等量价结构。
- 因子标准化后加权打分。
- 选择排名前 N 的股票等权或风险平价配置。
- 单票权重不超过 5%。
- 总仓位不超过 90%。
- 当市场指数处于长期均线下方时降低仓位。

这种策略不一定收益最高，但便于验证系统链路，适合作为第一条主线。

价格和成交量的详细认知框架见 `doc/price_volume_cognition.md`。

## 13. 系统运行流程

单股 CLI 分析流程：

```text
1. 用户输入股票代码
2. 从公开行情抓取最新日线数据
3. 计算最近 21 个交易日的价格指标和成交量指标
4. 识别涨停缩量、放量突破、缩量回调、放量下跌等形态
5. 使用历史量价样本训练轻量机器学习模型
6. 预测下一个交易日买入成功概率
7. 输出买入、观察、持有或回避建议
8. 输出对应理由、风险提示、机器学习预测和后续观察点
```

每日盘后流程：

```text
1. 更新交易日历和股票基础信息
2. 从公开行情采集当日行情和财务增量数据
3. 保存公开行情原始响应和采集任务状态
4. 解析并标准化数据
5. 数据质量检查
6. 计算因子
7. 生成策略信号
8. 生成下一交易日目标仓位
9. 风控检查
10. 输出调仓计划
11. 生成日报
```

盘中或开盘前流程：

```text
1. 读取目标仓位
2. 同步账户和持仓
3. 生成订单
4. 下单前风控
5. 执行订单
6. 记录成交
7. 检查持仓偏差
8. 发送执行报告
```

## 14. 质量保障

建议从一开始就建立测试：

- 公开行情采集测试：请求参数、限速、重试、异常响应处理。
- 公开行情解析测试：HTML、JSON、空数据、字段缺失和字段变更。
- 数据清洗测试：复权、缺失值、交易日对齐。
- 因子计算测试：窗口计算、排序、标准化。
- 回测测试：手续费、滑点、调仓、涨跌停。
- 风控测试：仓位限制、订单限制、亏损限制。
- 回归测试：固定数据和固定策略应得到固定结果。

同时建议保存每次回测配置：

```text
experiment_id
strategy_name
git_commit
data_version
start_date
end_date
parameters
metrics
created_at
```

这样可以避免“这次结果为什么和上次不一样”的常见问题。

## 15. 下一步落地建议

最直接的下一步是把项目初始化为标准 Python 包，并完成以下内容：

- `pyproject.toml`
- `src/quant_system/`
- `config/base.yaml`
- `quant advise <股票代码>` 单股分析 CLI
- 数据 schema
- 回测引擎骨架
- 一个多因子策略样例
- 一个 `scripts/run_backtest.py`

完成后，这个项目就能从架构设计进入可运行的研究系统。

CLI 的详细设计见 `doc/cli_design.md`。
