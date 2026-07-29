# Quantitative-System

Quantitative-System 是一个面向 A 股的本地量化研究与组合复盘工具。它使用免登录公开行情接口，提供量价分析、持仓风险判断、股票扫描、时间顺序模型验证、单股回测、SQLite 组合账本、风险指标和白色 Web 仪表盘。

本项目不是自动交易系统：不连接券商账户，不生成或下达真实委托，不承诺盈利。模型分数、胜率和回测结果都只能作为研究证据，不能替代公告核查、仓位管理和独立判断。

## 当前能力

- 数据采集：模型与回测固定使用腾讯前复权日线；组合估值固定使用不复权日线，并可在腾讯与新浪之间兜底；股票列表来自新浪，日级资金流来自东方财富。
- 原始留痕：日线和股票列表 HTTP 响应按来源和日期写入本地文件，保存 URL、抓取时间和内容哈希。
- 数据质量：拒绝日期倒序、重复日期、非法 OHLC、非有限价格和负成交量。
- 单股分析：技术指标、量价形态、机器学习分数、资金流、背驰、买入观察理由和风险提示。
- 持仓判断：根据成本价、目标收益和最大亏损约束输出持有、减仓、止盈或止损提示。
- 扫描：股票池扫描、底背离低成交额入场扫描，以及 Web 全 A 单轮扫描。
- 模型验证：时间顺序 walk-forward；验证段选择阈值，冻结后在测试段报告 Precision、基准达标率、Lift、Wilson 95% 区间、Coverage、平均净收益、Brier 和样本数。
- 样本外门禁：入选交易过少返回 `NOT_READY`，净收益或 Lift 未通过返回 `FAILED_OOS`，其余也仅为 `RESEARCH_ONLY`。
- 单股回测：次日开盘成交、费用和滑点、板块涨跌停、期末平仓、完整交易、净值和风险收益指标。
- SQLite：保存证券、标准化日线、投资组合、交易流水、持仓和组合净值。
- 组合复盘：创建组合、录入 BUY/SELL 流水、重放现金和持仓、刷新行情并重建净值。
- 组合风险：年化波动率、Sharpe、Beta、历史 VaR(95%)、完整持仓周期胜率和盈亏比；样本不足返回空值及原因。
- Web：白色总览，以及行情中心、策略研究、策略回测、投资组合四个工作页。
- 部署：支持本地 Python、Docker 镜像和 Docker Compose 持久卷。

## 明确边界

- 不自动下单，不连接券商，不管理真实资金。
- 不保证模型概率已经完成严格校准，也不把模型分数解释为收益率。
- 不使用登录、付费、验证码或绕过访问控制的数据源。
- 当前历史证券列表、历史 ST 状态和指数成分不完整，不能宣称消除了幸存者偏差。
- 日线回测不能完整模拟盘口排队、部分成交、冲击成本和盘中路径。
- SQLite 适合本地单用户和单容器部署，不是多用户高并发数据库。

最终发布还执行以下数据安全边界：

- 每次录入交易必须提供 `request_id`。同一组合内重复提交相同 `request_id` 只会命中已经保存的交易，不会重复扣款或增加持仓。
- 组合估值只读取 `daily_bars.adjustment_type = "NONE"` 的未复权行情，禁止将前复权研究价格与手工录入的真实成交价混用。
- 交易日期必须存在于该证券已经保存且通过质量校验的未复权日线中；仅格式正确或仅为工作日不足以通过校验。
- 当净值绑定的流水版本或未复权行情指纹落后于当前数据时，风险状态为 `STALE`；没有可用净值时为 `NO_DATA`。这两种状态都不会用旧指标或 `0` 冒充当前风险。
- 单项金额或价格不超过 `1,000,000,000,000` 元，数量不超过 `1,000,000,000` 股，成交金额换算为整数分后不得超过 SQLite 安全整数上限。

模型口径和研究风险详见 [模型验证口径与风险边界](doc/model_validation_and_risk.md)，当前工程与策略验收结论见 [V1 发布验收记录](doc/release_acceptance.md)。

## 项目结构

```text
quantitative-system/
├── config/                       # 运行环境与股票池示例
├── doc/                          # 中文设计和边界文档
├── scripts/
│   ├── quant                     # 本地 CLI/Web 启动脚本
│   └── docker-entrypoint.sh      # 容器迁移与启动
├── src/
│   ├── analysis/                 # 指标、形态、模型、walk-forward、评分和扫描
│   ├── api/                      # 组合、仪表盘、模型验证和回测响应
│   ├── backtest/                 # 单股回测与格式化
│   ├── data/                     # 公开数据源、原始留痕、质量和交易规则
│   ├── output/                   # CLI 分析输出
│   ├── portfolio/                # 交易重放、净值和风险
│   ├── storage/                  # SQLite 连接、迁移和仓储
│   ├── web.py                    # 本地 HTTP 服务与路由
│   └── web_static/               # 原生 HTML/CSS/JavaScript 前端
├── tests/                        # 稳定、无实时网络依赖的单元测试
├── Dockerfile
└── docker-compose.yml
```

## 本地运行

建议使用 Python 3.11。项目当前只使用 Python 标准库，无需安装额外运行依赖。

首次使用如果脚本没有执行权限：

```bash
chmod +x scripts/quant
```

查看单股建议：

```bash
./scripts/quant advise 600519
```

启动 Web：

```bash
./scripts/quant web --port 8000
```

浏览器打开：

[http://127.0.0.1:8000](http://127.0.0.1:8000)

默认端口 `8000` 被占用时，会依次尝试 `8001` 到 `8010`。也可以让系统自动选择：

```bash
./scripts/quant web --port 0
```

Web 启动时会自动执行幂等 SQLite 迁移。默认数据库和原始响应目录分别是：

```text
data/app/quantitative_system.db
data/raw/http/
```

可以在本地覆盖：

```bash
QUANT_DB_PATH=/tmp/quant.db \
PUBLIC_DATA_RAW_DIR=/tmp/quant-raw \
./scripts/quant web --port 8000
```

停止服务时按 `Ctrl+C`。

## CLI 命令

### 单股买入观察

```bash
./scripts/quant advise 600519
./scripts/quant advise 600519 --algorithm robust_ensemble
./scripts/quant advise 600519 --format json
```

支持算法：

- `robust_ensemble`：稳健集成，5 个交易日标签。
- `time_decay_knn`：时间衰减 KNN，5 个交易日标签。
- `knn`：普通 KNN，1 个交易日标签。
- `weighted_knn`：距离加权 KNN，1 个交易日标签。
- `logistic_regression`：轻量逻辑回归，1 个交易日标签。

### 持仓卖出风险

```bash
./scripts/quant sell 601988 --cost-price 5.20
./scripts/quant sell 601988 \
  --cost-price 5.20 \
  --quantity 1000 \
  --max-loss-rate 0.08 \
  --target-profit-rate 0.20
./scripts/quant sell 601988 --cost-price 5.20 --format json
```

### 股票池扫描

```bash
./scripts/quant scan --pool-file config/stock_pool.example.txt --once
./scripts/quant scan --symbols 601988,600519,600036 --once
./scripts/quant scan \
  --pool-file config/stock_pool.example.txt \
  --interval 600 \
  --top 10 \
  --min-score 52
```

### 底背离低成交额入场扫描

```bash
./scripts/quant entry-scan --pool-file config/stock_pool.example.txt --once
./scripts/quant entry-scan \
  --pool-file config/stock_pool.example.txt \
  --max-amount 80000000 \
  --max-amount-ratio 0.7 \
  --min-confidence 50 \
  --once
```

默认条件是下跌背驰候选、置信度不低于 `40`、当日成交额不高于 `5000` 万元、当日成交额与 20 日均额之比不高于 `0.8`。

### 时间顺序模型验证

```bash
./scripts/quant evaluate 600519
./scripts/quant evaluate 600519 --algorithm robust_ensemble
./scripts/quant evaluate 600519 --format json
```

`evaluate` 的验证段负责选择阈值，测试段只评估冻结阈值。测试输出包括：

- Precision：入选信号达标率。
- 基准达标率：测试段全部信号的达标率，用于判断筛选是否真正提高胜率。
- Lift：Precision / 基准达标率；大于 `1` 才表示入选样本的达标率高于无筛选基准。
- Precision 95% 区间：Wilson 区间，用于显示小样本不确定性。
- Coverage：入选信号占全部评估信号的比例。
- 平均净收益：固定持有期、扣比例交易成本后的入选信号平均收益。
- Brier：预测分数与二元标签的均方误差，越低越好。
- 测试样本数、入选数和达标数。

样本不足会明确报错，不返回虚假指标。

### 单股回测

```bash
./scripts/quant backtest 600519
./scripts/quant backtest 600519 \
  --buy-score 60 \
  --sell-risk 45 \
  --initial-cash 100000
./scripts/quant backtest 600519 \
  --commission-rate 0.0003 \
  --min-commission 5 \
  --stamp-duty-rate 0.0005 \
  --slippage-rate 0.0005 \
  --format json
```

如需保留期末未平仓头寸：

```bash
./scripts/quant backtest 600519 --no-liquidate-at-end
```

回测假设：

- 收盘后生成信号，下一交易日开盘成交。
- 买入含佣金和滑点；卖出含佣金、印花税和滑点。
- 成交价限制在当日日线最高价和最低价之间。
- 成交量为零或开盘价非法时不成交。
- 涨跌停按代码和日期近似区分主板、创业板、科创板和北交所。
- 默认按 `100` 股整手买入，默认期末按最后收盘价扣费平仓。
- 历史 ST 状态不完整，结果仍有交易规则偏差。

回测输出包括总收益、年化收益、最大回撤、完整交易胜率、Profit Factor、单笔期望、Sharpe、Calmar、资金暴露、成交和完整交易记录。没有完整交易时，相关指标返回空值。

## Web 页面

Web 包含一个总览和四个工作页：

1. 总览：组合总资产、当日收益、累计收益、最大回撤、净值与基准、资产配置、风险指标和状态。
2. 行情中心：单股分析、持仓卖出判断、K 线/成交量、股票池扫描和全 A 扫描。
3. 策略研究：执行 `evaluate`，展示冻结阈值和测试段 Precision、Coverage、平均净收益、Brier、样本数。
4. 策略回测：设置资金、评分阈值、费用、滑点和期末平仓，展示回测指标和成交记录。
5. 投资组合：创建组合、选择组合、录入 BUY/SELL、刷新行情、查看现金、持仓和流水。

侧栏“风险监控”会定位到总览中的风险面板，不是独立页面。没有组合或样本不足时，界面显示明确空状态，不填充虚构收益数字。

## SQLite 与组合账本

当前数据库共有 7 张表：

| 表 | 用途 |
|---|---|
| `schema_migrations` | 记录已执行迁移版本 |
| `instruments` | 股票、指数等证券主数据 |
| `daily_bars` | 以整数分保存的标准化日线 |
| `portfolios` | 组合名称、初始资金、起始日和基准 |
| `transactions` | BUY/SELL 流水、佣金和印花税 |
| `positions` | 由全部交易流水重放得到的当前持仓 |
| `portfolio_equity` | 现金、市值、总资产、净值、日收益和回撤 |

SQLite 开启外键、WAL 和 5 秒 busy timeout。金额和价格在数据库中使用整数分，净值和收益使用明确文本或结构化数值，避免用二进制浮点累计金额。

组合行为：

- 买入必须有足够现金，普通 A 股买入数量必须为 `100` 股整数倍。
- 卖出不能超过可用持仓。
- 交易请求通过同一组合内唯一的 `request_id` 幂等保存，客户端超时重试不会重复记账。
- 交易日期必须命中该证券已保存且已验证的 `NONE` 未复权日线。
- 新交易写入后重放全部流水，更新持仓。
- 刷新组合时拉取组合证券和基准日线，再重建净值。
- 组合估值只使用 `adjustment_type = "NONE"` 的未复权收盘价。
- 停牌或缺失交易日沿用最近已知收盘价，不读取未来价格。
- 行情刷新失败会返回警告，不会用未来价格补齐。
- 单项金额或价格上限为 `10^12` 元，数量上限为 `10^9` 股；价格乘数量换算为整数分后还必须处于 SQLite 安全整数范围内。

当前不持久化模型评估和单股回测结果，它们由请求即时计算。

## 风险指标

组合风险服务使用保存的净值、基准和已实现交易计算：

- 年化波动率：日收益样本标准差乘 `sqrt(252)`。
- Sharpe：当前默认年化无风险利率为 `0`。
- Beta：组合日收益与基准日收益协方差除以基准方差。
- 历史 VaR(95%)：日收益的历史 5% 分位损失。
- 完整持仓周期胜率：同一证券从首次买入到持仓归零形成一个周期，仅完整周期进入胜负统计；分批卖出不会被重复计数。
- 盈亏比：平均盈利除以平均亏损绝对值。

波动率、Sharpe、Beta 和 VaR 至少需要 20 个有效日收益样本。样本不足、缺少基准或没有已实现交易时返回 `null` 和原因，不使用 `0` 冒充有效结果。

风险响应还包含净值新鲜度状态。净值已落后于当前交易账本时返回 `STALE`，完全没有可用净值时返回 `NO_DATA`；前端应先展示状态和原因，再决定是否展示历史指标。

胜率不等于盈利。应同时查看平均盈亏、交易成本、最大回撤、Coverage 和资金暴露。

## Web API

### 系统、组合和仪表盘

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/health` | 服务与数据库健康检查 |
| `GET` | `/api/dashboard?portfolio_id=1` | 组合总览、净值、风险和资产配置 |
| `GET` | `/api/portfolios` | 组合列表 |
| `POST` | `/api/portfolios` | 创建组合 |
| `GET` | `/api/portfolios/{id}` | 组合详情、持仓、流水和净值 |
| `POST` | `/api/portfolios/{id}/transactions` | 录入 BUY/SELL |
| `POST` | `/api/portfolios/{id}/refresh` | 刷新行情并重建净值 |

创建组合示例：

```bash
curl -X POST http://127.0.0.1:8000/api/portfolios \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "研究组合",
    "initial_cash": 100000,
    "base_date": "2026-01-05",
    "benchmark_symbol": "000300"
  }'
```

录入交易示例：

```bash
curl -X POST http://127.0.0.1:8000/api/portfolios/1/transactions \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "trade-20260106-600519-buy-001",
    "symbol": "600519",
    "side": "BUY",
    "trade_date": "2026-01-06",
    "price": 1500,
    "quantity": 100
  }'
```

`request_id` 由调用方生成，并在同一组合内保持唯一。网络超时后可以原样重试；服务会返回已经保存的交易，不会再次写入。交易日期必须已经存在于该证券通过质量校验的未复权日线中，因此历史补录前应先刷新对应行情。

### 研究、回测和行情

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/evaluate` | 时间顺序模型验证 |
| `POST` | `/api/backtest` | 单股策略回测 |
| `POST` | `/api/advise` | 单股买入观察 |
| `POST` | `/api/sell-advice` | 持仓卖出风险 |
| `POST` | `/api/scan` | 一次性股票池扫描 |
| `POST` | `/api/market-scan/start` | 启动全 A 单轮扫描 |
| `POST` | `/api/market-scan/stop` | 请求停止扫描 |
| `GET` | `/api/market-scan/status` | 获取扫描快照 |
| `GET` | `/api/market-scan/stream` | SSE 扫描进度 |

模型验证示例：

```bash
curl -X POST http://127.0.0.1:8000/api/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"600519","algorithm":"robust_ensemble"}'
```

## Docker 与 Compose

镜像使用 Python 3.11 slim，并以非 root 用户运行。容器启动时执行幂等数据库迁移，`/api/health` 用于健康检查。

直接使用 Compose：

```bash
docker compose up -d --build
```

如果本机安装的是旧版独立 Compose，可将命令中的 `docker compose` 替换为 `docker-compose`。

访问：

[http://127.0.0.1:8000](http://127.0.0.1:8000)

查看状态和日志：

```bash
docker compose ps
docker compose logs -f quant
```

停止容器：

```bash
docker compose down
```

`quant-data` 命名卷保存 SQLite、WAL 文件和原始 HTTP 响应，普通 `docker compose down` 不删除该卷。

如需自定义端口或公开行情参数：

```bash
cp .env.example .env
cp config/runtime.example.env config/runtime.env
docker compose up -d --build
```

Compose 中：

- 根目录 `.env` 中的 `QUANT_WEB_BIND`、`QUANT_WEB_PORT` 控制宿主机监听地址和端口，默认仅本机可访问。
- `config/runtime.env` 控制容器内公开行情、限速和扫描参数，不参与 Compose 端口插值。
- `QUANT_DB_PATH` 固定为 `/app/data/app/quantitative_system.db`。
- `PUBLIC_DATA_RAW_DIR` 固定为 `/app/data/raw/http`。

单独构建和运行镜像：

```bash
docker build -t quantitative-system:local .
docker run --rm \
  -p 127.0.0.1:8000:8000 \
  -v quant-data:/app/data \
  quantitative-system:local
```

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `QUANT_DB_PATH` | SQLite 文件路径 | `data/app/quantitative_system.db` |
| `PUBLIC_DATA_RAW_DIR` | 原始 HTTP 响应目录 | `data/raw/http` |
| `PUBLIC_DATA_USER_AGENT` | 请求 User-Agent | `Mozilla/5.0` |
| `PUBLIC_DATA_TIMEOUT_SECONDS` | 请求超时秒数 | `8` |
| `PUBLIC_DATA_RETRIES` | 日线请求重试次数 | `2` |
| `PUBLIC_DATA_RETRY_BASE_SECONDS` | 重试基础等待秒数 | `1` |
| `PUBLIC_REQUEST_INTERVAL_SECONDS` | 日线和列表请求限速间隔 | `0.25` |
| `PUBLIC_KLINE_SOURCE` | 不复权组合行情的首选源：`tencent` 或 `sina` | `tencent` |
| `PUBLIC_STOCK_LIST_PAGE_SIZE` | 股票列表分页大小 | `80` |
| `PUBLIC_KLINE_DAYS` | 请求日线数量 | `260` |
| `MARKET_SCAN_WORKERS` | Web 全市场扫描并发数 | `4` |
| `MARKET_TASK_TIMEOUT_SECONDS` | 单只股票扫描超时秒数 | `30` |
| `QUANT_WEB_BIND` | Compose 宿主机监听地址 | `127.0.0.1` |
| `QUANT_WEB_PORT` | Compose 宿主机端口 | `8000` |

公开接口限流时可以降低请求速度：

```bash
PUBLIC_REQUEST_INTERVAL_SECONDS=1 ./scripts/quant web --port 8000
```

## 数据来源与复权边界

- 模型、指标和回测日线：固定使用腾讯前复权序列；腾讯失败时明确报错，不用不复权数据跨口径兜底。
- 组合估值日线：固定使用不复权序列；腾讯与新浪只在同一不复权口径内兜底，数据库记录为 `NONE`。
- 股票列表：新浪公开 A 股列表。
- 资金流：东方财富单股日级资金流。

抓取逻辑包含超时、限速和有限重试。分析序列与组合估值序列分开读取，禁止把前复权与不复权数据写入同一组合净值序列。组合账本仍未自动处理分红、送转和拆并股，发生公司行动时必须复核并修正持仓流水。

原始响应默认写入：

```text
data/raw/http/{source}/{YYYYMMDD}/{time}_{hash}.json
```

文件包含来源、URL、抓取时间、SHA-256 和原始正文。数据目录不应提交到 Git。

## 开发与测试

运行全部测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

检查前端脚本：

```bash
node --check src/web_static/app.js
```

检查 Python 语法：

```bash
PYTHONPATH=src python3 -m compileall -q src tests
```

测试数据小而确定，不依赖实时网络。

## 文档索引

- [V1 发布验收记录](doc/release_acceptance.md)：自动化、HTTP、Docker 和真实样本外验收结论。
- [当前系统架构](doc/quantitative_system_architecture.md)：实际模块、API、数据库和运行数据流。
- [模型验证口径与风险边界](doc/model_validation_and_risk.md)：标签、walk-forward、指标和偏差边界。
- [前后端与部署实施计划](doc/frontend_backend_implementation_plan.md)：初始建设计划和阶段目标。
- [CLI 设计](doc/cli_design.md)：命令与输出约定。
- [股票指标](doc/stock_indicators.md)：指标、形态和评分。
- [资金流设计](doc/fund_flow_design.md)：资金流数据与评分接入。
- [背驰信号设计](doc/divergence_signal_design.md)：背驰识别与入场扫描。
- [卖出决策设计](doc/sell_decision_design.md)：持仓卖出风险判断。
- [量价认知边界](doc/price_volume_cognition.md)：量价信号的解释边界。

## 自动交易与合规提示

本仓库不包含自动交易、券商连接、订单路由或实盘风控网关。

如果未来把信号接入券商接口，并由计算机程序自动生成或下达交易指令，应在实施前向券商和专业合规人员确认程序化交易报告、先报告后交易、异常交易监控、系统安全和交易所实施细则等要求。可参考：

- [中国证监会《证券市场程序化交易管理规定（试行）》](https://www.csrc.gov.cn/csrc/c100028/c7480577/content.shtml)。
- [上海证券交易所程序化交易管理实施细则](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20250612_10781696.shtml)。
- [深圳证券交易所程序化交易管理实施细则](https://www.szse.cn/lawrules/rule/allrules/bussiness/t20250403_612770.html)。
- [上海证券交易所交易规则（2026 年修订）](https://big5.sse.com.cn/site/cht/www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)。
- [深圳证券交易所交易规则（2026 年修订）](https://www.szse.cn/lawrules/rule/trade/current/t20260424_620190.html)。

监管要求可能变化，以上仅为范围和风险提示，不构成法律意见。
