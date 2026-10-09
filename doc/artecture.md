# 项目结构与文件职责

下列结构列出项目的源码、配置、脚本、测试及文档。运行时数据单独说明，不展开版本控制元数据、缓存和本地环境文件。

当前网页开放总览、行情中心、买入卖出、自动化交易和系统设置，加载 `app.js`、`client.js`、`trade.js` 与 `automation.js`。买卖栏目仅核对草稿并打开客户端供手动操作；自动化栏目保存实盘规则草案并检查接入缺口，尚无执行器，后端始终阻断实盘启动。研究、账本、实验和前向模拟引擎及存档保留；对应旧页面不参与导航，实验与前向脚本不再自动加载。

## 项目文件

```text
quantitative-system/
├── AGENTS.md                        # 开发协作规范
├── README.md                        # 项目定位、当前能力与使用边界
├── pyproject.toml                   # Python 项目元信息、依赖与应用入口
├── Dockerfile                       # 容器镜像构建、运行用户与健康检查
├── docker-compose.yml               # 容器服务、端口、环境变量与数据卷配置
├── .dockerignore                    # 容器构建时排除的文件
├── .gitignore                       # Git 忽略的数据、缓存和本地配置
├── .env.example                     # 容器服务监听地址和端口的配置示例
├── config/                          # 配置示例
│   ├── research.example.json        # 组合策略、模拟执行与研究验收参数
│   ├── runtime.example.env          # 行情请求、限速、重试与扫描参数
│   └── stock_pool.example.txt       # 股票池代码示例
├── doc/                             # 项目结构说明
│   └── artecture.md                 # 本文档：目录结构及文件职责
├── scripts/                         # 启动与验收脚本
│   ├── quant                       # 优先使用项目虚拟环境，分派命令行与 Web 服务
│   ├── docker-entrypoint.sh         # 容器启动前初始化数据库，再启动 Web 服务
│   ├── benchmark_market_scan.py    # 固定合成I/O场景比较4/8并发与重复扫描缓存，不代表实际市场耗时
│   ├── check_research_ui.cjs        # 旧组合实验页面验收脚本，界面入口已停用，不属于当前五页验收
│   ├── check_forward_ui.cjs         # 旧前向模拟页面验收脚本，界面入口已停用，后端测试仍保留
│   ├── check_client_browser.cjs     # 隔离浏览器验证总览只读快照、过期与断网、自选持仓联动及桌面手机布局
│   ├── check_client_risk_ui.cjs     # 自动结构分析、公开行情授权、历史风险轮询、雷达像素及桌面手机回归
│   ├── check_simple_workbench.cjs  # 五页导航、逐帧Top10、九算法切换与旧响应隔离、故障及桌面手机验收
│   ├── check_trade_ui.cjs           # 隔离浏览器验证买卖草稿、复制、打开客户端、失效与断线及三种视口
│   ├── check_automation_ui.cjs      # DOM替身验证实盘草案、迟到响应、版本冲突、过期与异常，不访问真实账户
│   └── check_client_ui.cjs          # 自选与持仓独立状态、过期阻断、分析带入及自动读取互斥测试
├── src/                             # 应用源码
│   ├── __init__.py                  # 源码包标识
│   ├── cli.py                      # 分析、扫描、回测、研究、数据体检和带预期版本的前向控制入口
│   ├── web.py                      # HTTP 服务、接口路由、静态资源与扫描进度推送
│   ├── desktop/                    # 仅在本机开放的同花顺只读连接
│   │   ├── __init__.py              # 本机连接包标识
│   │   ├── macos.py                 # 固定客户端启动、版本和权限检查、隔离进程内的只读辅助功能采集
│   │   ├── tonghuashun.py           # 同源校验、双通道快照、公开行情授权与缓存、后台风险与卖出建议、草稿核对及旧任务隔离
│   │   ├── trading.py              # 买卖参数、资金余量及可卖数量检查，只生成未提交草稿
│   │   └── automation.py           # 实盘规则草案校验、版本化原子保存及接入缺口，不包含订单执行器
│   ├── data/                       # 行情获取、数据对象与质量校验
│   │   ├── __init__.py              # 数据包标识
│   │   ├── models.py                # 证券信息、日线和资金流数据对象
│   │   ├── errors.py                # 区分数据源不可用、数据刷新失败与质量异常
│   │   ├── source.py                # 分析日线、未复权日线、资金流与证券列表读取入口
│   │   ├── public_sources.py        # 公开行情限速/归档、四类榜单补足与校验、前复权备用源及同日同时段留档校验回退
│   │   ├── fund_flow.py             # 可选资金流超时、带期限缓存与连续故障退避
│   │   ├── industry.py              # 全市场行业板块分页、来源与日期、冲突检测、后台刷新及公开元数据缓存
│   │   ├── quality.py               # 日期顺序、OHLC、成交量及有限数值校验
│   │   └── market_rules.py          # 按证券代码、名称和日期推导涨跌停幅度与价格
│   ├── analysis/                   # 单股指标、信号与模型分析
│   │   ├── __init__.py              # 分析包标识
│   │   ├── indicators.py            # 均线、收益、成交量、MACD 与价格位置指标
│   │   ├── patterns.py              # 根据指标识别量价、趋势及 K 线形态
│   │   ├── ml_model.py              # 共用特征与扣费标签、九种算法分发、scikit-learn树模型/贝叶斯与样本保护
│   │   ├── evaluation.py            # 时间顺序验证、阈值选择、样本外指标与研究门禁
│   │   ├── divergence.py            # 价格、MACD、量能和资金流的背驰分析
│   │   ├── fund_flow.py             # 资金流信号、评分调整与解释
│   │   ├── advisor.py               # 汇总指标和模型，生成买入观察评分、证据与风险提示
│   │   ├── sell_advisor.py          # 逐股决策、成本提醒价、仓位、闭市信号、缺失原因与可卖数量的持仓建议
│   │   ├── scanner.py               # 股票池扫描、入场候选筛选与循环扫描
│   │   ├── market_scanner.py        # 四类候选池、过滤后配额、即时切换、有界线程、Top10、风险摘要及完整详情、故障待重试计数
│   │   └── service.py               # 串联行情读取、买入分析与持仓风险分析
│   ├── backtest/                   # 单股策略回测
│   │   ├── __init__.py              # 回测包标识
│   │   ├── engine.py                # 历史信号、模拟成交、费用、交易记录与净值指标
│   │   └── formatter.py             # 单股回测结果的文本与 JSON 输出
│   ├── research/                   # 多证券组合研究与模拟执行
│   │   ├── __init__.py              # 组合研究包标识
│   │   ├── config.py                # 策略、模拟执行与验收配置的定义和校验
│   │   ├── dataset.py               # 历史行情及证券池快照、可选声明日历、可见时点、数据指纹与合成样本
│   │   ├── capture.py               # 新浪未复权采集、历史成员与日历导入、采集窗口变化及冻结历史校验
│   │   ├── audit.py                 # 日历差异、缺日及跳价体检；独立重建资金持仓、费用、净值与逐股盈亏
│   │   ├── strategy.py              # 动量和趋势筛选、逆波动率权重与目标仓位
│   │   ├── execution.py             # 模拟成交、T+1、涨跌停、整手、费用、滑点与容量限制
│   │   ├── engine.py                # 连续组合账本、敞口检查、熔断、模拟订单与净值
│   │   ├── experiment.py            # 滚动选参、独立最终留出期、成本压力、重复实验提示、调研依据与留档
│   │   ├── monitoring.py            # 同期基准与超额、实际仓位、执行质量、样本门槛及只读前向证据检查
│   │   ├── paper.py                 # 前向模拟、预登记信号与风控优先级、版本冲突、暂停恢复及后台观察
│   │   ├── session_store.py         # 原子持久化、跨进程读写锁、连续版本链、提交索引校验及旧格式迁移
│   │   └── operations.py            # 独立运行心跳、期限检查、运行告警及近期版本元数据
│   ├── portfolio/                  # 手工组合账本、估值与风险统计
│   │   ├── __init__.py              # 组合包标识
│   │   ├── service.py               # 组合创建、交易录入、幂等校验、现金与持仓重放
│   │   ├── performance.py           # 根据流水和历史行情重建净值，并计算行情指纹
│   │   ├── holdings_risk.py         # 当前持仓结构风险、按总资产权重逐日重放的历史情景及闭市日期与缺失样本校验
│   │   └── risk.py                  # 波动率、Sharpe、Beta、VaR、胜率与风险等级
│   ├── storage/                    # SQLite 存储
│   │   ├── __init__.py              # 存储包标识
│   │   ├── connection.py            # 数据库路径解析、连接与 WAL 等连接配置
│   │   ├── migrations.py            # 数据表定义、版本迁移与幂等初始化
│   │   ├── models.py                # 证券、日线、组合、交易、持仓与净值记录对象
│   │   ├── market_repository.py      # 证券主数据和标准化日线的校验、读取与保存
│   │   └── portfolio_repository.py   # 组合、交易流水、持仓和净值的数据库读写
│   ├── api/                        # Web 请求校验与业务响应组装
│   │   ├── __init__.py              # 接口包标识
│   │   ├── portfolio.py             # 组合、交易、行情刷新、仪表盘与风险响应
│   │   ├── research.py              # 单股模型验证与回测请求处理
│   │   ├── experiments.py           # 组合实验请求、历史报告读取与归档完整性校验
│   │   └── paper.py                 # 本机前向创建、追加行情、运行控制与预期版本校验、完整只读视图
│   ├── output/                     # 分析结果格式化
│   │   ├── __init__.py              # 输出包标识
│   │   └── formatter.py             # 买入观察和卖出风险结果的文本与 JSON 输出
│   └── web_static/                 # 原生 Web 前端
│       ├── index.html              # 总览/行情/买卖/自动化/设置五页；旧模块标记隐藏且不可交互
│       ├── styles.css              # 页面样式、布局与响应式适配
│       ├── app.js                  # 五页导航、候选榜单及行内折叠详情、持仓卖出建议、图表、失败和过期状态；旧页面函数不再初始化
│       ├── experiments.js          # 保留的旧实验与共享图表交互，不再由当前网页加载
│       ├── client.js               # 设置页连接与权限、可见页自动同步、风险/建议轮询及过期阻断
│       ├── trade.js                # 买卖表单、草稿核对与失效、逐字段复制及手动客户端衔接，不提交交易
│       ├── automation.js           # 实盘配置表单、接入检查与未保存输入保护，启动按钮不可用
│       └── paper.js                # 保留的旧前向模拟交互，不再由当前网页加载
└── tests/                          # 自动化测试
    ├── test_advisor.py              # 指标、模型、形态、资金流、背驰及分析结果测试
    ├── test_api.py                  # 组合与单股研究接口、输入校验和数据安全测试
    ├── test_backtest.py             # 单股成交时点、费用、涨跌停、T+1 与收益统计测试
    ├── test_cli.py                  # 命令行参数、调用分派与输出测试
    ├── test_data_quality.py         # 行情质量、来源口径、未收盘日线与原始归档测试
    ├── test_decisions.py            # Given/When/Then 的榜单结论保留、亏损优先、本地/历史持仓建议与资金隐私
    ├── test_desktop_bridge.py       # Given/When/Then 的双通道快照、分组完整性、权限、失败保留及 HTTP 安全
    ├── test_trade_ticket.py         # Given/When/Then 的草稿校验、资金与可卖限制、断线及每次重新读取
    ├── test_live_automation.py      # Given/When/Then 的实盘草案校验、存档损坏、版本冲突及启动始终阻断
    ├── test_holdings_risk.py        # Given/When/Then 的结构与历史风险、总资产权重、缺日闭市、授权撤销、缓存及异步竞争
    ├── test_industry.py             # 行业JSON解析、分页、公开缓存重载、分类冲突与未知值
    ├── test_market_workbench.py     # 逐股持仓建议、提醒价、慢股隔离、完成事件/长轮询回放、Top10及缓存失效
    ├── test_market_rank.py          # 四类池过滤后补位、全池不足与成功计数、跨页校验、任务切换及旧结果隔离
    ├── test_scan_performance.py     # 资金流退避/缓存过期、行情源故障、留档身份/哈希/时段校验及持仓授权隔离
    ├── test_market_rules.py         # 板块、风险警示状态和日期相关涨跌停规则测试
    ├── test_model_evaluation.py     # 模型标签、交易成本、新算法复现/缺类/训练边界与时间顺序研究门禁
    ├── test_portfolio.py            # 组合交易、现金持仓重放、净值与幂等行为测试
    ├── test_research.py             # 历史快照、组合执行、风控、未来数据隔离与实验复现测试
    ├── test_forward.py              # Given/When/Then 场景：最终留出期、真实数据边界、冻结账本、并发、定时观察与 HTTP 权限
    ├── test_operational.py          # Given/When/Then 场景：数据体检、独立对账、存档中断损坏、恢复、旧格式迁移及心跳状态
    ├── test_monitoring.py           # Given/When/Then 场景：基准对齐、样本边界、执行体检、风控优先、读写冲突及采集窗口
    ├── test_risk.py                 # 组合风险指标、样本不足与风险等级测试
    ├── test_storage.py              # 数据库迁移、记录校验与存储读写测试
    └── test_web.py                  # 请求编码、算法目录一致性、单股算法分发/真实训练与依赖错误测试
```

## 运行时数据

以下文件由运行过程生成，不作为源码提交。实验编号、来源和日期目录按实际运行变化。

```text
data/
├── app/
│   ├── quantitative_system.db      # 证券、日线、组合、流水、持仓和净值数据库
│   ├── live_automation.json         # 用户保存的实盘规则草案及版本；不含凭据、运行状态或交易授权
│   └── .live-plan.lock              # 实盘规则草案写入互斥锁
├── raw/
│   └── http/                       # 按来源和日期保存的原始公开行情响应
├── cache/
│   └── industries.json             # 全市场公开行业分类与来源、读取时间，不包含账户或持仓清单
├── snapshots/
│   └── <name>.json                 # 采集或导入的未复权研究快照，含来源、成员与已知日期
├── paper/
│   └── <session_id>/               # 独立的前向模拟，不使用同花顺账号资金
│       ├── state.json              # 最新完整存档的索引副本，含冻结数据、账本、信号与体检；损坏时禁止推进
│       ├── revisions/
│       │   └── <revision>.json     # 连续编号的完整状态存档、操作名、前序哈希与内容哈希；不自动回退
│       ├── head.json               # 最新提交编号与哈希，检测最新存档丢失
│       ├── runtime.json            # 独立进程心跳、工作状态与到期时间，不计入资金账本
│       ├── .lock                   # 跨进程更新互斥锁
│       └── .watch.lock             # 单个观察进程的运行期互斥锁
└── experiments/
    └── <run_id>/                   # 单次组合研究实验
        ├── dataset.json            # 实验使用的行情及历史证券池快照
        ├── config.json             # 实验冻结的策略、执行和验收配置
        ├── report.json             # 指标、候选参数、订单、净值和风险事件
        ├── report.md               # 便于阅读的实验摘要与限制说明
        └── manifest.json           # 归档文件的内容哈希清单
```
