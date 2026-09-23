# 项目结构与文件职责

下列结构列出项目的源码、配置、脚本、测试及文档。运行时数据单独说明，不展开版本控制元数据、缓存和本地环境文件。

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
│   ├── quant                       # 本地应用启动入口，分派命令行与 Web 服务
│   ├── docker-entrypoint.sh         # 容器启动前初始化数据库，再启动 Web 服务
│   ├── check_research_ui.cjs        # 组合实验页面的桌面、手机与交互验收
│   └── check_client_ui.cjs          # 只读连接页面的离线状态、过期、安全文本及自动同步测试
├── src/                             # 应用源码
│   ├── __init__.py                  # 源码包标识
│   ├── cli.py                      # 命令行参数解析与分析、扫描、回测、研究流程入口
│   ├── web.py                      # HTTP 服务、接口路由、静态资源与扫描进度推送
│   ├── desktop/                    # 仅在本机开放的同花顺只读连接
│   │   ├── __init__.py              # 本机连接包标识
│   │   ├── macos.py                 # 固定客户端启动、版本和权限检查、隔离进程内的只读辅助功能采集
│   │   └── tonghuashun.py           # 同源校验、资金持仓解析、完整性校验、内存快照及过期状态
│   ├── data/                       # 行情获取、数据对象与质量校验
│   │   ├── __init__.py              # 数据包标识
│   │   ├── models.py                # 证券信息、日线和资金流数据对象
│   │   ├── errors.py                # 数据刷新失败与质量异常定义
│   │   ├── source.py                # 分析日线、未复权日线、资金流与证券列表读取入口
│   │   ├── public_sources.py        # 公开行情请求、解析、超时、限速、重试与原始响应归档
│   │   ├── fund_flow.py             # 公开资金流数据请求、解析与缓存
│   │   ├── quality.py               # 日期顺序、OHLC、成交量及有限数值校验
│   │   └── market_rules.py          # 按证券代码、名称和日期推导涨跌停幅度与价格
│   ├── analysis/                   # 单股指标、信号与模型分析
│   │   ├── __init__.py              # 分析包标识
│   │   ├── indicators.py            # 均线、收益、成交量、MACD 与价格位置指标
│   │   ├── patterns.py              # 根据指标识别量价、趋势及 K 线形态
│   │   ├── ml_model.py              # 特征、持有期标签、相似样本模型、逻辑回归与集成预测
│   │   ├── evaluation.py            # 时间顺序验证、阈值选择、样本外指标与研究门禁
│   │   ├── divergence.py            # 价格、MACD、量能和资金流的背驰分析
│   │   ├── fund_flow.py             # 资金流信号、评分调整与解释
│   │   ├── advisor.py               # 汇总指标和模型，生成买入观察评分、证据与风险提示
│   │   ├── sell_advisor.py          # 根据持仓成本、收益目标与技术信号判断卖出风险
│   │   ├── scanner.py               # 股票池扫描、入场候选筛选与循环扫描
│   │   ├── market_scanner.py        # 全市场扫描任务、并发处理、进度状态与停止控制
│   │   └── service.py               # 串联行情读取、买入分析与持仓风险分析
│   ├── backtest/                   # 单股策略回测
│   │   ├── __init__.py              # 回测包标识
│   │   ├── engine.py                # 历史信号、模拟成交、费用、交易记录与净值指标
│   │   └── formatter.py             # 单股回测结果的文本与 JSON 输出
│   ├── research/                   # 多证券组合研究与模拟执行
│   │   ├── __init__.py              # 组合研究包标识
│   │   ├── config.py                # 策略、模拟执行与验收配置的定义和校验
│   │   ├── dataset.py               # 历史行情及证券池快照、可见时点、数据指纹与合成样本
│   │   ├── strategy.py              # 动量和趋势筛选、逆波动率权重与目标仓位
│   │   ├── execution.py             # 模拟成交、T+1、涨跌停、整手、费用、滑点与容量限制
│   │   ├── engine.py                # 连续组合账本、敞口检查、熔断、模拟订单与净值
│   │   └── experiment.py            # 滚动选参、样本外验证、成本压力测试、研究门禁与实验留档
│   ├── portfolio/                  # 手工组合账本、估值与风险统计
│   │   ├── __init__.py              # 组合包标识
│   │   ├── service.py               # 组合创建、交易录入、幂等校验、现金与持仓重放
│   │   ├── performance.py           # 根据流水和历史行情重建净值，并计算行情指纹
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
│   │   └── experiments.py           # 组合实验请求、历史报告读取与归档完整性校验
│   ├── output/                     # 分析结果格式化
│   │   ├── __init__.py              # 输出包标识
│   │   └── formatter.py             # 买入观察和卖出风险结果的文本与 JSON 输出
│   └── web_static/                 # 原生 Web 前端
│       ├── index.html              # 页面结构、导航、表单、表格与图表容器
│       ├── styles.css              # 页面样式、布局与响应式适配
│       ├── app.js                  # 总览、行情、模型验证、单股回测与组合管理交互
│       ├── experiments.js          # 组合实验提交、结果图表、订单翻页与报告导出
│       └── client.js               # 客户端启动、授权状态、只读同步、过期提示与内存快照清空
└── tests/                          # 自动化测试
    ├── test_advisor.py              # 指标、模型、形态、资金流、背驰及分析结果测试
    ├── test_api.py                  # 组合与单股研究接口、输入校验和数据安全测试
    ├── test_backtest.py             # 单股成交时点、费用、涨跌停、T+1 与收益统计测试
    ├── test_cli.py                  # 命令行参数、调用分派与输出测试
    ├── test_data_quality.py         # 行情质量、来源口径、未收盘日线与原始归档测试
    ├── test_desktop_bridge.py       # Given/When/Then 场景驱动的快照、权限、失败保留及 HTTP 安全测试
    ├── test_market_rules.py         # 板块、风险警示状态和日期相关涨跌停规则测试
    ├── test_model_evaluation.py     # 模型标签、交易成本、时间顺序验证与研究门禁测试
    ├── test_portfolio.py            # 组合交易、现金持仓重放、净值与幂等行为测试
    ├── test_research.py             # 历史快照、组合执行、风控、未来数据隔离与实验复现测试
    ├── test_risk.py                 # 组合风险指标、样本不足与风险等级测试
    ├── test_storage.py              # 数据库迁移、记录校验与存储读写测试
    └── test_web.py                  # HTTP 请求 JSON 与编码校验测试
```

## 运行时数据

以下文件由运行过程生成，不作为源码提交。实验编号、来源和日期目录按实际运行变化。

```text
data/
├── app/
│   └── quantitative_system.db      # 证券、日线、组合、流水、持仓和净值数据库
├── raw/
│   └── http/                       # 按来源和日期保存的原始公开行情响应
└── experiments/
    └── <run_id>/                   # 单次组合研究实验
        ├── dataset.json            # 实验使用的行情及历史证券池快照
        ├── config.json             # 实验冻结的策略、执行和验收配置
        ├── report.json             # 指标、候选参数、订单、净值和风险事件
        ├── report.md               # 便于阅读的实验摘要与限制说明
        └── manifest.json           # 归档文件的内容哈希清单
```
