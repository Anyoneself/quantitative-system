# Quantitative-System

Quantitative-System 是一个面向 A 股的本地量化研究与组合复盘系统。它将行情分析、策略验证、模拟成交和持仓复盘串联起来，帮助判断一个投资想法是否具有可检验的依据，以及收益背后承担了哪些风险。

项目的重点不是给出一个买卖分数，而是让每次研究能够回答：信号来自哪里、当时能否获得这些信息、交易是否可能成交、扣除成本后表现如何，以及结果能否复现。

## 快速启动

### 环境要求

Python 3.10 及以上；Python 运行与测试不依赖任何第三方库。唯一例外是 Windows：代码使用 `ZoneInfo("Asia/Shanghai")`，而 Windows 不自带 IANA 时区数据库，需要补装时区数据，否则启动会在导入阶段直接失败。macOS 与 Linux 自带，可跳过。`scripts/check_*_ui.cjs` 的浏览器验收脚本是可选工具，额外需要 Node.js 与 Playwright。

```bash
python -m pip install tzdata   # 仅 Windows 需要
```

### 启动 Web 界面

```bash
git clone https://github.com/Anyoneself/quantitative-system.git
cd quantitative-system
export PYTHONPATH=src          # Windows PowerShell：$env:PYTHONPATH = "src"
python -m web                  # 默认 http://127.0.0.1:8000
```

浏览器打开 <http://127.0.0.1:8000> 即可使用；左侧边栏可进入总览、行情中心、策略研究、回测分析、组合管理、组合实验、客户端连接与系统设置。

- 首次启动会自动初始化数据库，默认落在 `data/app/quantitative_system.db`，可用 `QUANT_DB_PATH` 指定其他位置。
- 默认端口被占用时会自动顺延尝试 8001 至 8010；也可以显式指定，例如 `python -m web --host 127.0.0.1 --port 8080`。
- 服务默认只监听本机回环地址，不对外网开放。
- macOS 与 Linux 还可以用 `./scripts/quant web` 启动，Windows 在 Git Bash 中用法相同，在 PowerShell 或 cmd 中则用 `scripts\quant.cmd web`；两者都会自动设置 `PYTHONPATH`。
- 安装为本地命令后可直接使用 `quant-web`：`python -m pip install -e .`。

### 使用命令行

```bash
python -m cli advise 600519                  # 单股买卖观察
python -m cli sell 600519 --cost-price 1500  # 持仓卖出风险
python -m cli backtest 600519                # 单股回测
python -m cli evaluate 600519                # 走步验证与样本外表现
python -m cli scan --pool-file config/stock_pool.example.txt --once
python -m cli research --demo                # 组合实验（合成数据，不代表真实业绩）
```

macOS 与 Linux 同样可以用 `./scripts/quant advise 600519` 等形式调用，Windows 用 `scripts\quant.cmd advise 600519`。`--demo` 使用合成数据，只用于验收工程链路，不是实际业绩。

### 使用容器

```bash
docker compose up --build
```

服务地址同为 <http://127.0.0.1:8000>；绑定地址与端口通过 `.env` 中的 `QUANT_WEB_BIND`、`QUANT_WEB_PORT` 调整（参考 `.env.example`），数据保存在 `quant-data` 卷。容器启动前会先执行幂等数据库迁移。

### 运行测试

```bash
export PYTHONPATH=src          # Windows PowerShell：$env:PYTHONPATH = "src"
python -m unittest discover -s tests -t tests
```

上述命令覆盖数据质量、分析、回测、组合实验、存储、接口与页面结构等测试。项目结构与各文件职责见 `doc/artecture.md`。

## 当前在做的事情

### 整理可信的研究数据

采集免登录公开行情和资金流，保留原始响应，检查日期、价格和成交量异常。区分研究使用的复权价格与组合估值使用的未复权价格；多证券研究要求明确的历史证券池快照，避免直接用当前股票池代替历史成员。

### 分析单股与持仓

结合技术指标、量价形态、资金流、背驰信号和机器学习分数，生成股票观察结果及风险提示。对于已有持仓，结合成本、目标收益和亏损约束，辅助复盘持有、减仓、止盈和止损的依据。

### 检验策略是否经得起验证

按时间顺序划分训练和测试阶段，在测试前冻结参数，观察未参与选参的数据上的表现。支持单股回测和多证券组合实验，关注成本后收益、回撤、波动、成交数量及不同时间阶段的一致性，而不只看历史胜率。

### 模拟交易约束与风险控制

模拟信号产生后的成交，考虑费用、滑点、停牌、涨跌停和 T+1 等约束。组合实验进一步限制仓位和成交容量，记录部分成交、拒单、回撤熔断、单日亏损暂停及行情陈旧事件，并比较成本上升后的策略表现。

### 复盘组合与保存研究证据

记录组合交易流水，重建现金、持仓和净值，展示资产配置与风险指标。每次组合实验保留所用数据、参数、订单、净值和验收结果，让成功与失败的研究都能够追溯和复现。

### 连接本机同花顺

支持启动本机同花顺、检查客户端和辅助功能权限，并从已登录的平安证券交易持仓页读取资金与持仓快照。快照单独展示，不导入模拟组合账本，不保存账号密码或登录令牌，也不保存到账户数据文件。支持手动读取与页面可见期间的定时读取，读取失败或超过有效期时明确标记旧数据。

当前适配 macOS 同花顺 5.3.5（7.0.62）的只读页面结构，客户端版本变化、权限不足、页面未就绪、金额隐藏或持仓市值无法核对时停止更新。这里同步的是客户端显示值，不保证行情实时性，也不将界面读取等同于官方账户接口。

### 系统设置

「系统设置」页集中本机环境说明，并提供同花顺客户端官方下载中心的 macOS 与 Windows 下载链接。链接只是来源指引，下载与安装由本机浏览器和用户自行完成；页面同时标明只读连接当前仅适配 macOS 的「应用程序/同花顺.app」，Windows 客户端尚不能被识别。

## 当前边界

- 系统用于研究、模拟、复盘及经用户授权的本机账户只读查询，不执行真实交易。
- 同花顺 OAuth 回调、自选股同步、行情流、成交回报、真实下单和撤单尚未接入；没有已验证接口的操作保持阻断。
- 客户端读取需要用户自行完成登录并授权 macOS 辅助功能；账户唯一标识尚未验证，快照不得用于订单决策或跨账户对账。
- 尚未完成真实市场盈利验证和实时前向模拟，不承诺稳定盈利。
- 历史证券状态、公司行动和退市处理仍不完整，日线模拟也不能完整还原盘口与实际成交。
- 模型分数和历史回测结果不是收益承诺；合成演示数据不是实际业绩。
- 研究检查通过不等于具备实盘条件，数据不足或验证失败会明确展示。
