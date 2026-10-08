const form = document.querySelector("#advisor-form");
const scanForm = document.querySelector("#scan-form");
const symbolInput = document.querySelector("#symbol");
const algorithmInput = document.querySelector("#algorithm");
const costPriceInput = document.querySelector("#cost-price");
const quantityInput = document.querySelector("#quantity");
const maxLossRateInput = document.querySelector("#max-loss-rate");
const targetProfitRateInput = document.querySelector("#target-profit-rate");
const scanAlgorithmInput = document.querySelector("#scan-algorithm");
const button = document.querySelector("#submit-button");
const scanAutoButton = document.querySelector("#scan-auto-button");
const singleTab = document.querySelector("#single-tab");
const scanTab = document.querySelector("#scan-tab");
const emptyState = document.querySelector("#empty-state");
const loadingState = document.querySelector("#loading-state");
const errorState = document.querySelector("#error-state");
const adviceView = document.querySelector("#advice-view");
const scanView = document.querySelector("#scan-view");
const globalSymbolInput = document.querySelector("#global-symbol");
const toast = document.querySelector("#toast");
const evaluationForm = document.querySelector("#evaluation-form");
const backtestForm = document.querySelector("#backtest-form");
const portfolioCreateForm = document.querySelector("#portfolio-create-form");
const transactionForm = document.querySelector("#transaction-form");
const portfolioSelect = document.querySelector("#portfolio-select");

let marketEventSource = null;
let currentPage = "overview";
let currentMarketMode = "single";
let equityPayload = null;
let toastTimer = null;
let currentPortfolioId = null;
let portfoliosLoaded = false;
let transactionSubmissionInProgress = false;
let pendingTransactionRequest = null;
let dashboardRequestVersion = 0;
let portfolioDetailRequestVersion = 0;

const actionText = {
  BUY: "建议买入观察",
  WATCH: "建议继续观察",
  HOLD: "信号中性",
  AVOID: "暂不建议买进",
  NO_DATA: "数据不足",
};

const dashboardRangeDays = {
  "1m": 31,
  "3m": 93,
  ytd: null,
  "1y": 366,
};

setupNavigation();
setupDashboard();
setupMarketActions();
setupEvaluationActions();
setupBacktestActions();
setupPortfolioActions();
setTradeDate();
setDefaultFormDates();
loadDashboard();

function setupNavigation() {
  document.querySelectorAll("[data-page]").forEach((node) => {
    node.addEventListener("click", () => navigateTo(node.dataset.page));
  });

  document.querySelectorAll("[data-feature]").forEach((node) => {
    node.addEventListener("click", () => {
      showToast(`${node.dataset.feature}将在对应后端能力完成后开放。`);
    });
  });

  document.querySelector("#menu-button").addEventListener("click", () => {
    document.body.classList.add("sidebar-open");
  });
  document.querySelector("#mobile-close").addEventListener("click", closeSidebar);
  document.querySelector("#sidebar-backdrop").addEventListener("click", closeSidebar);

  document.querySelector("#global-search").addEventListener("submit", (event) => {
    event.preventDefault();
    const symbol = globalSymbolInput.value.trim();
    if (!isValidSymbol(symbol)) {
      showToast("请输入 6 位股票代码。");
      globalSymbolInput.focus();
      return;
    }
    symbolInput.value = symbol;
    navigateTo("market");
    setMode("single");
    form.requestSubmit();
  });

  document.querySelector("#refresh-button").addEventListener("click", () => {
    if (currentPage === "overview") {
      loadDashboard();
      return;
    }
    if (currentPage === "portfolios") {
      loadPortfolios(currentPortfolioId);
      return;
    }
    if (currentPage === "experiments") {
      loadExperimentHistory();
      return;
    }
    if (currentPage === "client") {
      loadClientConnection();
      return;
    }
    if (currentPage === "research" || currentPage === "backtest") {
      showToast("重新提交当前表单即可刷新最新公开行情结果。");
      return;
    }
    if (currentMarketMode === "scan") {
      refreshMarketScanStatus();
      return;
    }
    showToast("请输入股票代码并重新分析，以刷新最新公开行情。");
  });
}

function setupDashboard() {
  document.querySelector("#go-market-button").addEventListener("click", () => navigateTo("market"));
  document.querySelector("#onboarding-market-button").addEventListener("click", () => navigateTo("market"));
  document.querySelectorAll("[data-range]").forEach((node) => {
    node.addEventListener("click", () => {
      document.querySelectorAll("[data-range]").forEach((item) => item.classList.toggle("active", item === node));
      renderEquityChart(equityPayload, node.dataset.range);
    });
  });
  window.addEventListener("resize", debounce(() => {
    if (currentPage === "overview") {
      const activeRange = document.querySelector("[data-range].active")?.dataset.range || "ytd";
      renderEquityChart(equityPayload, activeRange);
    } else if (currentPage === "market" && !adviceView.classList.contains("hidden") && adviceView.dataset.chart) {
      drawStockChart(JSON.parse(adviceView.dataset.chart));
    }
  }, 120));
}

function setupMarketActions() {
  singleTab.addEventListener("click", () => setMode("single"));
  scanTab.addEventListener("click", () => setMode("scan"));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!isValidSymbol(symbolInput.value.trim())) {
      showMarketError("股票代码必须是 6 位数字。");
      symbolInput.focus();
      return;
    }

    setState("loading");
    button.disabled = true;
    button.textContent = "分析中...";

    try {
      const response = await fetch("/api/advise", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: symbolInput.value.trim(),
          algorithm: algorithmInput.value,
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(readError(payload, "分析失败"));
      }
      renderAdvice(payload);
      await loadSellAdvice();
      setState("result");
      updateGlobalTime(payload.advice?.indicators?.analysis_date);
      requestAnimationFrame(() => drawStockChart(payload.chart));
    } catch (error) {
      showMarketError(error.message);
    } finally {
      button.disabled = false;
      button.textContent = "开始分析";
    }
  });

  scanForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await startMarketScan();
  });

  scanAutoButton.addEventListener("click", async () => {
    await stopMarketScan();
  });
}

function navigateTo(page) {
  const targetPage = page === "risk" ? "overview" : page;
  if (!["overview", "market", "research", "backtest", "portfolios", "experiments", "client", "settings"].includes(targetPage)) return;
  if (currentPage === "market" && targetPage !== "market") {
    stopMarketUpdates();
  }
  currentPage = targetPage;
  document.querySelectorAll("[data-page-panel]").forEach((node) => {
    node.classList.toggle("active", node.dataset.pagePanel === targetPage);
  });
  document.querySelectorAll(".nav-item[data-page]").forEach((node) => {
    node.classList.toggle("active", node.dataset.page === page);
  });
  closeSidebar();
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (targetPage === "overview") {
    const activeRange = document.querySelector("[data-range].active")?.dataset.range || "ytd";
    requestAnimationFrame(() => renderEquityChart(equityPayload, activeRange));
    loadDashboard();
  }
  if (targetPage === "portfolios" && !portfoliosLoaded) {
    loadPortfolios();
  }
  if (targetPage === "experiments") {
    loadExperimentHistory();
    requestAnimationFrame(drawExperimentChart);
  }
  if (targetPage === "client") loadClientConnection();
  if (page === "risk") {
    window.setTimeout(() => {
      document.querySelector("#risk-panel")?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 80);
  }
}

function closeSidebar() {
  document.body.classList.remove("sidebar-open");
}

async function loadDashboard() {
  const requestVersion = ++dashboardRequestVersion;
  const requestedPortfolioId = currentPortfolioId;
  const refreshButton = document.querySelector("#refresh-button");
  refreshButton.disabled = true;
  refreshButton.textContent = "⋯";
  try {
    const dashboardUrl = currentPortfolioId
      ? `/api/dashboard?portfolio_id=${encodeURIComponent(currentPortfolioId)}`
      : "/api/dashboard";
    const response = await fetch(dashboardUrl, {
      headers: { Accept: "application/json" },
    });
    if (
      requestVersion !== dashboardRequestVersion
      || requestedPortfolioId !== currentPortfolioId
    ) return;
    if (!response.ok) {
      if (response.status === 404) {
        setDashboardEmptyState();
        return;
      }
      throw new Error("仪表盘数据暂不可用");
    }
    const payload = await response.json();
    if (
      requestVersion !== dashboardRequestVersion
      || requestedPortfolioId !== currentPortfolioId
    ) return;
    if (payload.ok === false) {
      throw new Error(readError(payload, "仪表盘数据暂不可用"));
    }
    renderDashboard(payload.dashboard || payload);
  } catch (error) {
    if (
      requestVersion !== dashboardRequestVersion
      || requestedPortfolioId !== currentPortfolioId
    ) return;
    setDashboardEmptyState();
    showToast(error.message);
  } finally {
    if (requestVersion === dashboardRequestVersion) {
      refreshButton.disabled = false;
      refreshButton.textContent = "↻";
    }
  }
}

function setDashboardEmptyState() {
  equityPayload = null;
  setText("#total-assets", "--");
  setText("#daily-profit", "--");
  setText("#total-return", "--");
  setText("#max-drawdown", "--");
  setText("#total-assets-note", "尚未创建或选择组合");
  setText("#daily-profit-note", "暂无有效净值");
  setText("#total-return-note", "暂无组合收益记录");
  setText("#max-drawdown-note", "净值样本不足");
  setText("#equity-benchmark-label", "比较基准");
  setText("#equity-empty-message", "创建组合并录入交易后，这里会显示净值与比较基准。");
  resetRisk();
  resetAllocation();
  renderStrategyRows([]);
  renderEquityChart(null, "ytd");
  setText("#trade-date", "--");
  setText("#global-update-time", "尚无组合数据");
}

function renderDashboard(dashboard) {
  const summary = dashboard.summary || {};
  const hasPortfolio = Boolean(dashboard.portfolio || summary.total_assets !== null && summary.total_assets !== undefined);
  setText("#total-assets", formatCurrency(summary.total_assets));
  setText("#daily-profit", formatSignedCurrency(summary.daily_profit));
  setText("#total-return", formatOptionalPercent(summary.total_return));
  setText("#max-drawdown", formatOptionalPercent(summary.max_drawdown));
  setText("#total-assets-note", summary.total_assets_note || (hasPortfolio ? "组合最新估值" : "尚未创建组合"));
  setText("#daily-profit-note", summary.daily_profit_note || (hasPortfolio ? "相较上一交易日" : "暂无有效净值"));
  setText("#total-return-note", summary.total_return_note || (hasPortfolio ? "相较组合初始净值" : "暂无组合收益记录"));
  setText("#max-drawdown-note", summary.max_drawdown_note || (hasPortfolio ? "历史净值最大跌幅" : "净值样本不足"));
  const benchmarkSymbol = dashboard.portfolio?.benchmark_symbol;
  setText(
    "#equity-benchmark-label",
    benchmarkSymbol ? `基准 ${benchmarkSymbol}` : "比较基准",
  );
  setText(
    "#equity-empty-message",
    dashboard.risk?.stale
      ? "净值与当前流水或行情版本不一致，请在组合页执行刷新。"
      : hasPortfolio
        ? "录入交易并刷新行情后，这里会显示净值与比较基准。"
        : "创建组合并录入交易后，这里会显示净值与比较基准。",
  );

  equityPayload = dashboard.equity || null;
  const activeRange = document.querySelector("[data-range].active")?.dataset.range || "ytd";
  renderEquityChart(equityPayload, activeRange);
  renderRisk(dashboard.risk || {});
  renderAllocation(dashboard.allocation || []);
  renderStrategyRows(dashboard.strategies || []);
  if (dashboard.updated_at || summary.trade_date) {
    updateGlobalTime(dashboard.updated_at || summary.trade_date);
  } else {
    setText("#global-update-time", "尚无组合数据");
  }
  setText("#trade-date", summary.trade_date ? `交易日 ${summary.trade_date}` : "--");
}

function renderRisk(risk) {
  setText("#risk-volatility", formatOptionalPercent(risk.annualized_volatility));
  setText("#risk-sharpe", formatOptionalNumber(risk.sharpe_ratio));
  setText("#risk-beta", formatOptionalNumber(risk.beta));
  setText("#risk-var", formatOptionalPercent(risk.var_95));
  setText("#risk-win-rate", formatOptionalPercent(risk.win_rate));
  setText("#risk-profit-loss", formatOptionalNumber(risk.profit_loss_ratio));
  setText("#risk-sample-status", risk.sample_status || "样本不足");
  setText("#risk-state", risk.risk_level_text || risk.risk_level || "待计算");
  setText("#risk-state-note", risk.explanation || "暂无足够数据计算组合风险");
  setText("#risk-radar > strong", risk.risk_level_text || "待计算");
}

function resetRisk() {
  [
    "#risk-volatility",
    "#risk-sharpe",
    "#risk-beta",
    "#risk-var",
    "#risk-win-rate",
    "#risk-profit-loss",
  ].forEach((selector) => setText(selector, "--"));
  setText("#risk-sample-status", "样本不足");
  setText("#risk-state", "待计算");
  setText("#risk-state-note", "创建组合并录入交易后开始计算");
  setText("#risk-radar > strong", "待计算");
}

function renderAllocation(items) {
  resetAllocation();
  if (!Array.isArray(items) || items.length === 0) return;
  const validItems = items.filter((item) => Number(item.ratio) > 0);
  if (validItems.length === 0) return;
  const colors = ["#155eef", "#08aeb1", "#f4a51c", "#7357df", "#92a0b5"];
  let progress = 0;
  const stops = validItems.map((item, index) => {
    const start = progress;
    progress += Number(item.ratio) * 100;
    return `${colors[index % colors.length]} ${start}% ${progress}%`;
  });
  const donut = document.querySelector(".empty-donut");
  donut.style.background = `radial-gradient(circle, #fff 0 46%, transparent 47%), conic-gradient(${stops.join(",")})`;
  donut.querySelector("strong").textContent = formatCurrency(
    validItems.reduce((total, item) => total + Number(item.amount || 0), 0),
  );
  document.querySelector(".allocation-empty > p").textContent = validItems
    .map((item) => `${item.name} ${formatOptionalPercent(item.ratio)}`)
    .join(" · ");
}

function resetAllocation() {
  const donut = document.querySelector(".empty-donut");
  donut.style.background = "";
  donut.querySelector("strong").textContent = "--";
  document.querySelector(".allocation-empty > p").textContent = "暂无持仓数据";
}

function renderStrategyRows(strategies) {
  const tbody = document.querySelector("#strategy-table");
  tbody.innerHTML = "";
  if (!Array.isArray(strategies) || strategies.length === 0) {
    appendEmptyRow(tbody, 6, "暂无已保存的策略结果，请先执行样本外验证或回测。");
    return;
  }
  strategies.forEach((strategy) => {
    const row = document.createElement("tr");
    const nameCell = document.createElement("td");
    const name = document.createElement("strong");
    const code = document.createElement("small");
    name.textContent = strategy.name || "未命名策略";
    code.textContent = strategy.strategy_type || "";
    nameCell.append(name, code);
    row.appendChild(nameCell);
    appendCell(row, strategy.status_text || "可用");
    appendCell(row, formatOptionalPercent(strategy.annualized_return));
    appendCell(row, formatOptionalNumber(strategy.sharpe_ratio));
    appendCell(row, formatOptionalPercent(strategy.max_drawdown));
    appendCell(row, strategy.horizon_text || "--");
    tbody.appendChild(row);
  });
}

function renderEquityChart(payload, range) {
  const canvas = document.querySelector("#equity-chart");
  const empty = document.querySelector("#equity-empty");
  const points = normalizeEquityPayload(payload);
  if (points.length < 2) {
    empty.classList.remove("hidden");
    clearCanvas(canvas);
    return;
  }

  const filtered = filterEquityRange(points, range);
  if (filtered.length < 2) {
    empty.classList.remove("hidden");
    clearCanvas(canvas);
    return;
  }
  empty.classList.add("hidden");
  drawLineChart(canvas, filtered);
}

function normalizeEquityPayload(payload) {
  if (!payload) return [];
  if (Array.isArray(payload)) {
    return payload.map((item) => ({
      date: item.trade_date || item.date,
      strategy: Number(item.net_value ?? item.strategy),
      benchmark: optionalFiniteNumber(item.benchmark_net_value ?? item.benchmark),
    })).filter(validEquityPoint);
  }
  const dates = payload.dates || [];
  const strategy = payload.strategy || payload.net_values || [];
  const benchmark = payload.benchmark || payload.benchmark_values || [];
  return dates.map((date, index) => ({
    date,
    strategy: Number(strategy[index]),
    benchmark: optionalFiniteNumber(benchmark[index]),
  })).filter(validEquityPoint);
}

function validEquityPoint(point) {
  return point.date && Number.isFinite(point.strategy);
}

function filterEquityRange(points, range) {
  if (!points.length) return points;
  const lastDate = new Date(points[points.length - 1].date);
  if (range === "ytd") {
    return points.filter((point) => new Date(point.date).getFullYear() === lastDate.getFullYear());
  }
  const days = dashboardRangeDays[range];
  if (!days) return points;
  const startTime = lastDate.getTime() - days * 86400000;
  return points.filter((point) => new Date(point.date).getTime() >= startTime);
}

function drawLineChart(canvas, points) {
  const ctx = prepareCanvas(canvas);
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const pad = { left: 43, right: 16, top: 22, bottom: 30 };
  const values = points
    .flatMap((point) => [point.strategy, point.benchmark])
    .filter((value) => Number.isFinite(value));
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const margin = Math.max((maxValue - minValue) * 0.14, 0.02);
  const low = minValue - margin;
  const high = maxValue + margin;

  ctx.lineWidth = 1;
  ctx.font = "10px sans-serif";
  ctx.fillStyle = "#8a95a6";
  ctx.strokeStyle = "#edf0f5";
  for (let index = 0; index < 5; index += 1) {
    const ratio = index / 4;
    const y = pad.top + ratio * (height - pad.top - pad.bottom);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    const value = high - ratio * (high - low);
    ctx.fillText(value.toFixed(2), 5, y + 3);
  }

  drawSeries(ctx, points, "benchmark", "#08aeb1", width, height, pad, low, high, false);
  drawSeries(ctx, points, "strategy", "#155eef", width, height, pad, low, high, true);

  const labelIndexes = [0, Math.floor((points.length - 1) / 2), points.length - 1];
  labelIndexes.forEach((index, labelIndex) => {
    const x = pad.left + (index / (points.length - 1)) * (width - pad.left - pad.right);
    ctx.textAlign = labelIndex === 0 ? "left" : labelIndex === 2 ? "right" : "center";
    ctx.fillStyle = "#8a95a6";
    ctx.fillText(points[index].date, x, height - 8);
  });
  ctx.textAlign = "left";
}

function drawSeries(ctx, points, key, color, width, height, pad, low, high, withFill) {
  const x = (index) => pad.left + (index / (points.length - 1)) * (width - pad.left - pad.right);
  const y = (value) => pad.top + (1 - (value - low) / (high - low)) * (height - pad.top - pad.bottom);

  ctx.beginPath();
  let hasStarted = false;
  points.forEach((point, index) => {
    if (!Number.isFinite(point[key])) {
      hasStarted = false;
      return;
    }
    if (!hasStarted) ctx.moveTo(x(index), y(point[key]));
    else ctx.lineTo(x(index), y(point[key]));
    hasStarted = true;
  });

  if (withFill) {
    const gradient = ctx.createLinearGradient(0, pad.top, 0, height - pad.bottom);
    gradient.addColorStop(0, "rgba(21, 94, 239, 0.16)");
    gradient.addColorStop(1, "rgba(21, 94, 239, 0)");
    ctx.save();
    ctx.lineTo(x(points.length - 1), height - pad.bottom);
    ctx.lineTo(x(0), height - pad.bottom);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();
    ctx.restore();
    ctx.beginPath();
    hasStarted = false;
    points.forEach((point, index) => {
      if (!Number.isFinite(point[key])) {
        hasStarted = false;
        return;
      }
      if (!hasStarted) ctx.moveTo(x(index), y(point[key]));
      else ctx.lineTo(x(index), y(point[key]));
      hasStarted = true;
    });
  }

  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.stroke();
}

function setupEvaluationActions() {
  evaluationForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const symbol = document.querySelector("#evaluation-symbol").value.trim();
    if (!isValidSymbol(symbol)) {
      showFeatureError("evaluation", "股票代码必须是 6 位数字。");
      return;
    }

    const submitButton = document.querySelector("#evaluation-submit");
    setFeatureState("evaluation", "loading");
    setButtonBusy(submitButton, true, "验证中...");
    try {
      const payload = await requestJson("/api/evaluate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol,
          algorithm: document.querySelector("#evaluation-algorithm").value,
        }),
      });
      renderEvaluation(payload);
      setFeatureState("evaluation", "result");
      updateGlobalTime();
    } catch (error) {
      showFeatureError("evaluation", error.message);
    } finally {
      setButtonBusy(submitButton, false, "执行样本外验证");
    }
  });
}

function renderEvaluation(payload) {
  const evaluation = payload.evaluation || payload;
  const test = evaluation.test || {};
  const precision = optionalFiniteNumber(test.precision);
  const baseRate = optionalFiniteNumber(test.base_rate);
  setText("#evaluation-threshold", formatOptionalPercent(evaluation.threshold));
  setText("#evaluation-precision", formatOptionalPercent(precision));
  setText("#evaluation-base-rate", formatOptionalPercent(baseRate));
  setText("#evaluation-lift", formatOptionalMultiple(test.lift));
  setText(
    "#evaluation-precision-ci",
    formatPercentInterval(test.precision_ci_low, test.precision_ci_high),
  );
  setText(
    "#evaluation-precision-delta",
    precision !== null && baseRate !== null
      ? `较基准 ${formatPercentagePointDelta(precision - baseRate)}`
      : "比较模型筛选后与全部测试样本",
  );
  setText("#evaluation-coverage", formatOptionalPercent(test.coverage));
  setText("#evaluation-net-return", formatOptionalPercent(test.average_net_return));
  setText("#evaluation-brier", formatOptionalNumber(test.brier_score, 4));
  setText("#evaluation-samples", formatOptionalInteger(test.sample_count));
  setText(
    "#evaluation-selected",
    `入选 ${formatOptionalInteger(test.selected_count)} · 达标 ${formatOptionalInteger(test.positive_count)}`,
  );
  setText("#evaluation-algorithm-name", algorithmLabel(evaluation.algorithm));
  setText("#evaluation-horizon", evaluation.horizon_days ? `${evaluation.horizon_days} 个交易日` : "--");
  setText(
    "#evaluation-validation-range",
    formatDateRange(evaluation.validation_start_date, evaluation.validation_end_date),
  );
  setText("#evaluation-test-range", formatDateRange(evaluation.test_start_date, evaluation.test_end_date));
  const releaseStatus = evaluation.release_status || "RESEARCH_ONLY";
  const releaseStatusText = {
    NOT_READY: "样本不足，不可发布",
    FAILED_OOS: "样本外未通过",
    RESEARCH_ONLY: "仅供研究",
  }[releaseStatus] || releaseStatus;
  setText("#evaluation-release-status", releaseStatusText);
  setText(
    "#evaluation-release-reason",
    evaluation.release_reason || "样本外评估仅供研究，不构成可交易结论。",
  );
  const releaseBadge = document.querySelector("#evaluation-release-badge");
  releaseBadge.classList.toggle("blocked", releaseStatus !== "RESEARCH_ONLY");
  releaseBadge.classList.toggle("research", releaseStatus === "RESEARCH_ONLY");
  releaseBadge.lastChild.textContent = releaseStatusText;
  setText(
    "#evaluation-disclaimer",
    payload.disclaimer || "测试段只用于评估冻结阈值，不代表未来收益，也不应据此继续调参。",
  );
  setMetricTone(
    "#evaluation-precision",
    precision !== null && baseRate !== null ? precision - baseRate : null,
  );
  const lift = optionalFiniteNumber(test.lift);
  setMetricTone("#evaluation-lift", lift === null ? null : lift - 1);
  setMetricTone("#evaluation-net-return", test.average_net_return);
}

function setupBacktestActions() {
  backtestForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const symbol = document.querySelector("#backtest-symbol").value.trim();
    if (!isValidSymbol(symbol)) {
      showFeatureError("backtest", "股票代码必须是 6 位数字。");
      return;
    }

    let body;
    try {
      body = {
        symbol,
        algorithm: document.querySelector("#backtest-algorithm").value,
        initial_cash: readNumberInput("#backtest-initial-cash"),
        buy_score: readNumberInput("#backtest-buy-score"),
        sell_risk: readNumberInput("#backtest-sell-risk"),
        max_position_rate: readNumberInput("#backtest-position-rate"),
        commission_rate: readNumberInput("#backtest-commission"),
        min_commission: readNumberInput("#backtest-min-commission"),
        stamp_duty_rate: readNumberInput("#backtest-stamp-duty"),
        slippage_rate: readNumberInput("#backtest-slippage"),
        liquidate_at_end: document.querySelector("#backtest-liquidate").checked,
      };
    } catch (error) {
      showFeatureError("backtest", error.message);
      return;
    }

    const submitButton = document.querySelector("#backtest-submit");
    setFeatureState("backtest", "loading");
    setButtonBusy(submitButton, true, "回测中...");
    try {
      const payload = await requestJson("/api/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      renderBacktest(payload);
      setFeatureState("backtest", "result");
      updateGlobalTime();
    } catch (error) {
      showFeatureError("backtest", error.message);
    } finally {
      setButtonBusy(submitButton, false, "运行回测");
    }
  });
}

function renderBacktest(payload) {
  const result = payload.backtest || payload.result || payload;
  setText("#backtest-total-return", formatOptionalPercent(result.total_return));
  setText("#backtest-final-equity", `期末权益 ${formatCurrency(result.final_equity)}`);
  setText("#backtest-max-drawdown", formatOptionalPercent(result.max_drawdown));
  setText("#backtest-win-rate", formatOptionalPercent(result.win_rate));
  setText("#backtest-closed-count", `完整交易 ${formatOptionalInteger(result.closed_trade_count)}`);
  setText("#backtest-profit-factor", formatOptionalNumber(result.profit_factor));
  setText("#backtest-sharpe", formatOptionalNumber(result.sharpe_ratio));
  setText("#backtest-calmar", formatOptionalNumber(result.calmar_ratio));
  setText("#backtest-date-range", formatDateRange(result.start_date, result.end_date));
  setText("#backtest-annualized-return", formatOptionalPercent(result.annualized_return));
  setText("#backtest-expectancy", formatCurrency(result.expectancy));
  setText("#backtest-exposure", formatOptionalPercent(result.exposure_rate));
  setText("#backtest-order-count", formatOptionalInteger(result.trade_count));
  setText("#backtest-open-count", formatOptionalInteger(result.open_position_count));
  setMetricTone("#backtest-total-return", result.total_return);
  setMetricTone("#backtest-annualized-return", result.annualized_return);
  renderBacktestTrades(result.trades || []);

  const notes = Array.isArray(result.notes) ? result.notes : [];
  const disclaimer = payload.disclaimer || "";
  setText(
    "#backtest-disclaimer",
    [disclaimer, notes.length ? `回测假设：${notes.join("；")}` : ""].filter(Boolean).join(" "),
  );
}

function renderBacktestTrades(trades) {
  const tbody = document.querySelector("#backtest-trades");
  tbody.innerHTML = "";
  if (!Array.isArray(trades) || trades.length === 0) {
    appendEmptyRow(tbody, 8, "本次回测没有产生成交。");
    return;
  }
  trades.forEach((trade) => {
    const row = document.createElement("tr");
    appendCell(row, trade.signal_date);
    appendCell(row, trade.trade_date);
    appendTradeSideCell(row, trade.side);
    appendCell(row, formatOptionalNumber(trade.price, 4));
    appendCell(row, formatOptionalInteger(trade.quantity));
    appendCell(row, formatCurrency(trade.amount));
    appendCell(row, formatCurrency(trade.fee));
    appendCell(row, trade.reason || "--");
    tbody.appendChild(row);
  });
}

function setupPortfolioActions() {
  portfolioCreateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = document.querySelector("#portfolio-name").value.trim();
    const benchmark = document.querySelector("#portfolio-benchmark").value.trim();
    if (!name) {
      showPortfolioError("组合名称不能为空。");
      return;
    }
    if (!isValidSymbol(benchmark)) {
      showPortfolioError("比较基准必须是 6 位数字代码。");
      return;
    }

    const submitButton = document.querySelector("#portfolio-create-submit");
    setButtonBusy(submitButton, true, "创建中...");
    try {
      const payload = await requestJson("/api/portfolios", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          initial_cash: readNumberInput("#portfolio-initial-cash"),
          base_date: document.querySelector("#portfolio-base-date").value,
          benchmark_symbol: benchmark,
        }),
      });
      document.querySelector("#portfolio-name").value = "";
      showToast("组合已创建。");
      await loadPortfolios(payload.portfolio?.id);
      loadDashboard();
    } catch (error) {
      showPortfolioError(error.message);
    } finally {
      setButtonBusy(submitButton, false, "创建组合");
    }
  });

  portfolioSelect.addEventListener("change", () => {
    const portfolioId = Number(portfolioSelect.value);
    if (Number.isInteger(portfolioId) && portfolioId > 0) {
      currentPortfolioId = portfolioId;
      loadPortfolioDetail(portfolioId);
      loadDashboard();
    } else {
      showPortfolioEmpty();
    }
  });

  document.querySelector("#portfolio-refresh").addEventListener("click", async () => {
    if (!currentPortfolioId) {
      showPortfolioError("请先创建或选择组合。");
      return;
    }
    const refreshButton = document.querySelector("#portfolio-refresh");
    setButtonBusy(refreshButton, true, "刷新中...");
    try {
      const payload = await requestJson(`/api/portfolios/${currentPortfolioId}/refresh`, {
        method: "POST",
      });
      renderPortfolioDetail(payload);
      if (payload.warning) showToast(payload.warning);
      else showToast("组合行情与净值已刷新。");
      loadDashboard();
    } catch (error) {
      showPortfolioError(error.message);
    } finally {
      setButtonBusy(refreshButton, false, "刷新");
    }
  });

  transactionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (transactionSubmissionInProgress) return;
    if (!currentPortfolioId) {
      showPortfolioError("请先创建或选择组合。");
      return;
    }
    const symbol = document.querySelector("#transaction-symbol").value.trim();
    if (!isValidSymbol(symbol)) {
      showPortfolioError("股票代码必须是 6 位数字。");
      return;
    }

    let body;
    try {
      const side = document.querySelector("#transaction-side").value;
      const quantity = readNumberInput("#transaction-quantity");
      if (!Number.isInteger(quantity)) {
        throw new Error("成交数量必须是整数股。");
      }
      if (side === "BUY" && quantity % 100 !== 0) {
        throw new Error("A 股买入数量必须是 100 股的整数倍。");
      }
      body = {
        side,
        symbol,
        trade_date: document.querySelector("#transaction-date").value,
        price: readNumberInput("#transaction-price"),
        quantity,
        commission: readOptionalNumberInput("#transaction-commission"),
        stamp_tax: readOptionalNumberInput("#transaction-stamp-tax"),
        note: document.querySelector("#transaction-note").value.trim() || null,
      };
    } catch (error) {
      showPortfolioError(error.message);
      return;
    }

    const requestKey = JSON.stringify({
      portfolio_id: currentPortfolioId,
      ...body,
    });
    if (!pendingTransactionRequest || pendingTransactionRequest.key !== requestKey) {
      pendingTransactionRequest = {
        key: requestKey,
        requestId: createClientRequestId(),
      };
    }
    body.request_id = pendingTransactionRequest.requestId;
    const requestBody = JSON.stringify(body);
    const submitButton = document.querySelector("#transaction-submit");
    transactionSubmissionInProgress = true;
    setButtonBusy(submitButton, true, "保存中...");
    try {
      const payload = await requestJsonWithNetworkRetry(
        `/api/portfolios/${currentPortfolioId}/transactions`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: requestBody,
        },
      );
      pendingTransactionRequest = null;
      renderPortfolioDetail(payload);
      resetTransactionInputs();
      if (payload.warning) showToast(`交易已保存；${payload.warning}`);
      else showToast("交易已保存，持仓与现金已重算。");
      loadDashboard();
    } catch (error) {
      showPortfolioError(error.message);
    } finally {
      transactionSubmissionInProgress = false;
      setButtonBusy(submitButton, false, "保存交易");
    }
  });
}

async function loadPortfolios(preferredPortfolioId = null) {
  clearPortfolioError();
  try {
    const payload = await requestJson("/api/portfolios");
    const portfolios = Array.isArray(payload.portfolios) ? payload.portfolios : [];
    portfoliosLoaded = true;
    portfolioSelect.innerHTML = "";
    if (portfolios.length === 0) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "暂无组合";
      portfolioSelect.appendChild(option);
      document.querySelector("#portfolio-list-empty").classList.remove("hidden");
      showPortfolioEmpty();
      return;
    }

    document.querySelector("#portfolio-list-empty").classList.add("hidden");
    portfolios.forEach((portfolio) => {
      const option = document.createElement("option");
      option.value = String(portfolio.id);
      option.textContent = `${portfolio.name} · ${formatCurrency(portfolio.initial_cash)}`;
      portfolioSelect.appendChild(option);
    });
    const availableIds = portfolios.map((item) => Number(item.id));
    const targetId = Number(preferredPortfolioId || currentPortfolioId);
    currentPortfolioId = availableIds.includes(targetId) ? targetId : availableIds[0];
    portfolioSelect.value = String(currentPortfolioId);
    await loadPortfolioDetail(currentPortfolioId);
  } catch (error) {
    portfoliosLoaded = false;
    showPortfolioError(error.message);
    showPortfolioEmpty();
  }
}

async function loadPortfolioDetail(portfolioId) {
  const requestVersion = ++portfolioDetailRequestVersion;
  clearPortfolioError();
  try {
    const payload = await requestJson(`/api/portfolios/${portfolioId}`);
    if (
      requestVersion !== portfolioDetailRequestVersion
      || currentPortfolioId !== portfolioId
    ) return;
    currentPortfolioId = portfolioId;
    portfolioSelect.value = String(portfolioId);
    renderPortfolioDetail(payload);
  } catch (error) {
    if (
      requestVersion !== portfolioDetailRequestVersion
      || currentPortfolioId !== portfolioId
    ) return;
    showPortfolioError(error.message);
    showPortfolioEmpty();
  }
}

function renderPortfolioDetail(payload) {
  const portfolio = payload.portfolio || {};
  const summary = payload.summary || {};
  const positions = Array.isArray(payload.positions) ? payload.positions : [];
  const transactions = Array.isArray(payload.transactions) ? payload.transactions : [];
  if (portfolio.id) {
    currentPortfolioId = Number(portfolio.id);
    portfolioSelect.value = String(portfolio.id);
  }

  document.querySelector("#portfolio-detail-empty").classList.add("hidden");
  document.querySelector("#portfolio-detail").classList.remove("hidden");
  setText("#portfolio-cash", formatCurrency(summary.cash));
  setText("#portfolio-total-assets", formatCurrency(summary.total_assets));
  setText("#portfolio-initial-assets", formatCurrency(portfolio.initial_cash));
  setText("#portfolio-base-date-label", `起始日 ${portfolio.base_date || "--"}`);
  setText("#portfolio-position-count", formatOptionalInteger(summary.position_count ?? positions.length));
  setText(
    "#portfolio-transaction-count",
    `流水 ${formatOptionalInteger(summary.transaction_count ?? transactions.length)} 笔`,
  );
  const estimatedPosition = Boolean(summary.valuation_estimated)
    || positions.some((item) => item.estimated);
  setText(
    "#portfolio-valuation-note",
    positions.length === 0
      ? "当前仅含现金"
      : estimatedPosition
        ? "部分持仓暂无行情，按可用账面值"
        : `估值截止 ${summary.as_of_date || "--"}`,
  );
  renderPortfolioPositions(positions);
  renderPortfolioTransactions(transactions);
  clearPortfolioError();
  if (portfolio.updated_at) updateGlobalTime(portfolio.updated_at);
}

function renderPortfolioPositions(positions) {
  const tbody = document.querySelector("#portfolio-positions");
  tbody.innerHTML = "";
  if (positions.length === 0) {
    appendEmptyRow(tbody, 7, "当前组合没有持仓。");
    return;
  }
  positions.forEach((position) => {
    const row = document.createElement("tr");
    appendCell(row, position.symbol);
    appendCell(row, formatOptionalInteger(position.quantity));
    appendCell(row, formatCurrency(position.average_cost));
    appendCell(row, formatCurrency(position.latest_price));
    appendCell(row, formatCurrency(position.cost_basis));
    appendCell(row, formatCurrency(position.market_value));
    const profitCell = document.createElement("td");
    profitCell.textContent = formatSignedCurrency(position.unrealized_profit);
    const profit = Number(position.unrealized_profit);
    if (Number.isFinite(profit) && profit !== 0) {
      profitCell.classList.add(profit > 0 ? "positive-value" : "negative-value");
    }
    row.appendChild(profitCell);
    tbody.appendChild(row);
  });
}

function renderPortfolioTransactions(transactions) {
  const tbody = document.querySelector("#portfolio-transactions");
  tbody.innerHTML = "";
  if (transactions.length === 0) {
    appendEmptyRow(tbody, 7, "当前组合没有交易流水。");
    return;
  }
  transactions.forEach((transaction) => {
    const row = document.createElement("tr");
    appendCell(row, transaction.trade_date);
    appendTradeSideCell(row, transaction.side);
    appendCell(row, transaction.symbol);
    appendCell(row, formatCurrency(transaction.price));
    appendCell(row, formatOptionalInteger(transaction.quantity));
    appendCell(
      row,
      formatCurrency(Number(transaction.commission || 0) + Number(transaction.stamp_tax || 0)),
    );
    appendCell(row, transaction.note || "--");
    tbody.appendChild(row);
  });
}

function showPortfolioEmpty() {
  currentPortfolioId = null;
  document.querySelector("#portfolio-detail").classList.add("hidden");
  document.querySelector("#portfolio-detail-empty").classList.remove("hidden");
  document.querySelector("#portfolio-positions").innerHTML = "";
  document.querySelector("#portfolio-transactions").innerHTML = "";
}

function showPortfolioError(message) {
  const target = document.querySelector("#portfolio-error");
  target.textContent = message;
  target.classList.remove("hidden");
}

function clearPortfolioError() {
  const target = document.querySelector("#portfolio-error");
  target.textContent = "";
  target.classList.add("hidden");
}

function resetTransactionInputs() {
  ["#transaction-symbol", "#transaction-price", "#transaction-quantity", "#transaction-note"].forEach((selector) => {
    document.querySelector(selector).value = "";
  });
  document.querySelector("#transaction-commission").value = "";
  document.querySelector("#transaction-stamp-tax").value = "";
}

function setFeatureState(prefix, state) {
  document.querySelector(`#${prefix}-empty`).classList.toggle("hidden", state !== "empty");
  document.querySelector(`#${prefix}-loading`).classList.toggle("hidden", state !== "loading");
  document.querySelector(`#${prefix}-result`).classList.toggle("hidden", state !== "result");
  const error = document.querySelector(`#${prefix}-error`);
  error.textContent = "";
  error.classList.add("hidden");
}

function showFeatureError(prefix, message) {
  setFeatureState(prefix, "error");
  const target = document.querySelector(`#${prefix}-error`);
  target.textContent = message;
  target.classList.remove("hidden");
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(response.ok ? "服务返回了无法解析的数据。" : `请求失败（HTTP ${response.status}）。`);
  }
  if (!response.ok || payload?.ok === false) {
    throw new Error(readError(payload, `请求失败（HTTP ${response.status}）`));
  }
  return payload;
}

async function requestJsonWithNetworkRetry(url, options) {
  try {
    return await requestJson(url, options);
  } catch (error) {
    if (!(error instanceof TypeError)) throw error;
    return requestJson(url, options);
  }
}

function createClientRequestId() {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }

  const bytes = new Uint8Array(16);
  if (typeof cryptoApi?.getRandomValues === "function") {
    cryptoApi.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const value = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
}

function setButtonBusy(buttonNode, busy, label) {
  buttonNode.disabled = busy;
  buttonNode.textContent = label;
}

function readNumberInput(selector) {
  const input = document.querySelector(selector);
  const value = Number(input.value);
  if (!input.value.trim() || !Number.isFinite(value)) {
    throw new Error(`${input.closest("label")?.querySelector("span")?.textContent || "数值参数"}格式不正确。`);
  }
  return value;
}

function readOptionalNumberInput(selector) {
  const value = document.querySelector(selector).value.trim();
  if (!value) return null;
  const number = Number(value);
  if (!Number.isFinite(number)) {
    throw new Error("交易费用格式不正确。");
  }
  return number;
}

function setDefaultFormDates() {
  const today = localDateString(new Date());
  document.querySelector("#portfolio-base-date").value = today;
  document.querySelector("#transaction-date").value = today;
}

function localDateString(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatDateRange(start, end) {
  if (!start || !end) return "--";
  return `${start} 至 ${end}`;
}

function algorithmLabel(algorithm) {
  const labels = {
    robust_ensemble: "稳健集成模型",
    time_decay_knn: "时间衰减 KNN",
    knn: "KNN 相似样本",
    weighted_knn: "加权 KNN",
    logistic_regression: "逻辑回归",
  };
  return labels[algorithm] || algorithm || "--";
}

function appendTradeSideCell(row, side) {
  const cell = document.createElement("td");
  const badge = document.createElement("span");
  const normalizedSide = String(side || "").toUpperCase();
  badge.className = `trade-side ${normalizedSide === "BUY" ? "buy" : "sell"}`;
  badge.textContent = normalizedSide || "--";
  cell.appendChild(badge);
  row.appendChild(cell);
}

function appendEmptyRow(tbody, columnCount, message) {
  const row = document.createElement("tr");
  row.className = "table-empty-row";
  const cell = document.createElement("td");
  cell.colSpan = columnCount;
  cell.textContent = message;
  row.appendChild(cell);
  tbody.appendChild(row);
}

function formatOptionalInteger(value) {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  return Number.isFinite(number) ? String(Math.round(number)) : "--";
}

function optionalFiniteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function setMetricTone(selector, value) {
  const node = document.querySelector(selector);
  node.classList.remove("positive-value", "negative-value");
  const number = Number(value);
  if (!Number.isFinite(number) || number === 0) return;
  node.classList.add(number > 0 ? "positive-value" : "negative-value");
}

function setMode(mode) {
  currentMarketMode = mode;
  const isScan = mode === "scan";
  singleTab.classList.toggle("active", !isScan);
  scanTab.classList.toggle("active", isScan);
  form.classList.toggle("hidden", isScan);
  scanForm.classList.toggle("hidden", !isScan);
  if (isScan) {
    setState("scan");
    refreshMarketScanStatus();
    startMarketUpdates();
    return;
  }
  stopMarketUpdates();
  setState("empty");
}

async function loadSellAdvice() {
  const response = await fetch("/api/sell-advice", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol: symbolInput.value.trim(),
      cost_price: parseOptionalNumber(costPriceInput.value),
      quantity: parseOptionalNumber(quantityInput.value),
      max_loss_rate: parseOptionalNumber(maxLossRateInput.value) || 0.08,
      target_profit_rate: parseOptionalNumber(targetProfitRateInput.value) || 0.20,
    }),
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(readError(payload, "卖出判断失败"));
  }
  renderSellAdvice(payload.sell_advice);
}

function renderSellAdvice(sellAdvice) {
  setText("#sell-score", sellAdvice.sell_risk_score);
  setText("#sell-action-label", sellActionText(sellAdvice.action));
  const detail = document.querySelector("#sell-detail");
  detail.innerHTML = "";
  const rows = [
    ["卖出建议", sellActionText(sellAdvice.action)],
    ["卖出风险评分", String(sellAdvice.sell_risk_score)],
  ];
  if (sellAdvice.holding_return !== null && sellAdvice.holding_return !== undefined) {
    rows.push(["持仓收益率", formatPercent(sellAdvice.holding_return)]);
  }
  if (sellAdvice.key_levels) {
    rows.push(["20 日均线", formatNumber(sellAdvice.key_levels.ma20)]);
    rows.push(["年线 MA250", sellAdvice.key_levels.ma250 > 0 ? formatNumber(sellAdvice.key_levels.ma250) : "数据不足"]);
  }
  rows.forEach(([label, value]) => {
    const row = document.createElement("div");
    const labelNode = document.createElement("span");
    const valueNode = document.createElement("strong");
    labelNode.textContent = label;
    valueNode.textContent = value;
    row.append(labelNode, valueNode);
    detail.appendChild(row);
  });
  renderList("#sell-risks", [
    ...(sellAdvice.reasons || []),
    ...(sellAdvice.risks || []),
    ...(sellAdvice.observations || []),
  ]);
}

function sellActionText(action) {
  const actionMap = {
    HOLD: "继续持有",
    REDUCE: "减仓观察",
    TAKE_PROFIT: "止盈/分批减仓",
    STOP_LOSS: "止损",
    NO_POSITION: "仅技术风险提示",
  };
  return actionMap[action] || action;
}

function setState(state) {
  emptyState.classList.toggle("hidden", state !== "empty");
  loadingState.classList.toggle("hidden", state !== "loading");
  errorState.classList.add("hidden");
  adviceView.classList.toggle("hidden", state !== "result");
  scanView.classList.toggle("hidden", state !== "scan");
}

function showMarketError(message) {
  emptyState.classList.add("hidden");
  loadingState.classList.add("hidden");
  adviceView.classList.add("hidden");
  scanView.classList.add("hidden");
  errorState.textContent = message;
  errorState.classList.remove("hidden");
}

function renderAdvice(payload) {
  const advice = payload.advice;
  const indicators = advice.indicators;
  const prediction = advice.ml_prediction;
  const beginner = payload.beginner;

  setText("#stock-code", indicators.symbol);
  setText("#stock-name", indicators.name || "未知");
  setText("#stock-date", indicators.analysis_date);
  document.querySelector("#stock-link").href = `https://stockpage.10jqka.com.cn/${indicators.symbol}/`;
  setText("#headline", `${actionText[advice.action] || advice.action}：${beginner.headline}`);
  setText("#score", advice.score);
  setText("#buy-action-label", actionText[advice.action] || advice.action);
  setText("#close-price", formatNumber(indicators.close));
  setText("#return-1d", formatPercent(indicators.return_1d));
  setText("#return-5d", formatPercent(indicators.return_5d));
  setText("#volume-ratio", indicators.volume_ratio_20d.toFixed(2));
  setText("#ma250", indicators.ma250 > 0 ? formatNumber(indicators.ma250) : "数据不足");
  setText("#macd-summary", `${indicators.macd_dif >= indicators.macd_dea ? "多头" : "空头"} ${indicators.macd_histogram.toFixed(3)}`);
  setText("#first-day-high", indicators.first_day_high > 0 ? formatNumber(indicators.first_day_high) : "不适用");
  setText("#data-range", `${payload.chart.dates[payload.chart.dates.length - 1]} · ${payload.chart.dates.length} 个交易日`);

  renderList("#reasons", advice.reasons);
  renderList("#evidence", advice.evidence || []);
  renderList("#risks", advice.risks);
  renderList("#observations", advice.observations);
  renderMl(prediction);
  renderFundFlow(payload.fund_flow);
  renderDivergence(payload.divergence);
  adviceView.dataset.chart = JSON.stringify(payload.chart);
}

function renderDivergence(divergence) {
  if (!divergence) {
    setText("#divergence-adjustment", "未参与评分");
    setText("#divergence-signal", "样本不足");
    setText("#divergence-confidence", "--");
    setText("#divergence-buy", "--");
    setText("#divergence-sell", "--");
    setText("#divergence-explanation", "背驰样本不足，本次评分未纳入背驰信号。");
    return;
  }
  const buySign = divergence.bullish_score_adjustment > 0 ? "+" : "";
  const sellSign = divergence.bearish_risk_adjustment > 0 ? "+" : "";
  setText("#divergence-adjustment", `买入 ${buySign}${divergence.bullish_score_adjustment} / 卖出 ${sellSign}${divergence.bearish_risk_adjustment}`);
  setText("#divergence-signal", divergence.signal);
  setText("#divergence-confidence", String(divergence.confidence));
  setText("#divergence-buy", `${buySign}${divergence.bullish_score_adjustment}`);
  setText("#divergence-sell", `${sellSign}${divergence.bearish_risk_adjustment}`);
  setText("#divergence-explanation", divergence.reasons.join("；"));
}

function renderFundFlow(fundFlow) {
  if (!fundFlow) {
    setText("#fund-flow-adjustment", "未参与评分");
    setText("#fund-flow-signal", "不可用");
    setText("#fund-flow-latest", "--");
    setText("#fund-flow-5d", "--");
    setText("#fund-flow-10d", "--");
    setText("#fund-flow-explanation", "资金流数据暂不可用，本次评分未纳入资金流。");
    return;
  }
  const sign = fundFlow.fund_flow_score_adjustment > 0 ? "+" : "";
  setText("#fund-flow-adjustment", `资金流调整 ${sign}${fundFlow.fund_flow_score_adjustment}`);
  setText("#fund-flow-signal", fundFlow.signal);
  setText("#fund-flow-latest", formatAmount(fundFlow.latest_main_net_inflow));
  setText("#fund-flow-5d", formatAmount(fundFlow.main_inflow_5d));
  setText("#fund-flow-10d", formatAmount(fundFlow.main_inflow_10d));
  setText("#fund-flow-explanation", fundFlow.explanation);
}

async function startMarketScan() {
  setState("scan");
  const scanButton = document.querySelector("#scan-once-button");
  scanButton.disabled = true;
  scanButton.textContent = "启动中...";

  try {
    const response = await fetch("/api/market-scan/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        algorithm: scanAlgorithmInput.value,
        top: 10,
      }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(readError(payload, "启动扫描失败"));
    }
    renderMarketScan(payload.snapshot);
    startMarketUpdates();
    setState("scan");
  } catch (error) {
    showMarketError(error.message);
  } finally {
    scanButton.disabled = false;
    scanButton.textContent = "启动全 A 扫描";
  }
}

async function stopMarketScan() {
  try {
    await fetch("/api/market-scan/stop", { method: "POST" });
    stopMarketUpdates();
    setText("#scan-refresh-status", "已停止");
  } catch {
    showToast("停止请求失败，请稍后重试。");
  }
}

function startMarketUpdates() {
  if ("EventSource" in window) {
    startMarketStream();
  } else {
    refreshMarketScanStatus();
  }
}

function stopMarketUpdates() {
  stopMarketStream();
}

function startMarketStream() {
  if (marketEventSource) return;
  marketEventSource = new EventSource("/api/market-scan/stream");
  marketEventSource.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.ok) {
      renderMarketScan(payload.snapshot);
    }
  };
  marketEventSource.onerror = () => {
    stopMarketStream();
    refreshMarketScanStatus();
  };
}

function stopMarketStream() {
  if (!marketEventSource) return;
  marketEventSource.close();
  marketEventSource = null;
}

async function refreshMarketScanStatus() {
  try {
    const response = await fetch("/api/market-scan/status");
    const payload = await response.json();
    if (payload.ok) {
      renderMarketScan(payload.snapshot);
    }
  } catch {
    showToast("暂时无法读取扫描状态。");
  }
}

function renderMarketScan(snapshot) {
  const results = (snapshot.top_results || [])
    .sort((left, right) => right.score - left.score || right.probability - left.probability)
    .slice(0, 10);
  setText("#scan-count", results.length);
  setText("#scan-fetched", snapshot.fetched_count || 0);
  setText("#scan-finished", snapshot.scanned_count);
  setText("#scan-total", snapshot.total_count || "--");
  setText("#scan-skipped", snapshot.skipped_count || 0);
  setText("#scan-failed", snapshot.failed_count || 0);
  setText("#scan-refresh-status", statusText(snapshot));
  setText("#scan-meta", `MARKET SCAN · ${snapshot.last_started_at || "--"}`);

  const tbody = document.querySelector("#scan-results");
  tbody.innerHTML = "";
  results.forEach((item, index) => {
    const row = document.createElement("tr");
    appendCell(row, String(index + 1));
    const stockCell = document.createElement("td");
    const symbolNode = document.createElement("strong");
    const nameNode = document.createElement("small");
    symbolNode.textContent = item.symbol;
    nameNode.textContent = item.name;
    stockCell.append(symbolNode, nameNode);
    row.appendChild(stockCell);
    appendCell(row, actionText[item.action] || item.action);
    appendCell(row, String(item.score));
    appendCell(row, formatPercent(item.probability));
    appendCell(row, item.reason);
    appendCell(row, item.risk);
    tbody.appendChild(row);
  });
  const emptyNode = document.querySelector("#scan-empty");
  emptyNode.textContent = scanEmptyText(snapshot);
  emptyNode.classList.toggle("hidden", results.length !== 0);
  if (snapshot.last_started_at) updateGlobalTime(snapshot.last_started_at);
}

function statusText(snapshot) {
  return snapshot.status_message || snapshot.status;
}

function scanEmptyText(snapshot) {
  if ((snapshot.failed_count || 0) > 0) {
    return "暂时没有可展示股票，已有股票抓取失败或数据不足，请查看运行状态。";
  }
  if ((snapshot.skipped_count || 0) > 0) {
    return "暂时没有可展示股票，当前批次可能多为已按规则跳过的市场代码。";
  }
  return "正在等待第一只股票完成分析。";
}

function appendCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value ?? "--";
  row.appendChild(cell);
}

function renderList(selector, items) {
  const target = document.querySelector(selector);
  target.innerHTML = "";
  (items || []).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    target.appendChild(li);
  });
}

function renderMl(prediction) {
  const target = document.querySelector("#ml-detail");
  target.innerHTML = "";
  const rows = [
    ["算法", prediction.algorithm_name],
    ["预测周期", prediction.horizon_days > 1 ? `未来 ${prediction.horizon_days} 个交易日` : "下一交易日"],
    ["历史达标占比", formatPercent(prediction.buy_probability)],
    ["训练样本数", String(prediction.sample_count)],
  ];
  if (prediction.neighbor_count > 0) {
    rows.push(["最近邻样本数", String(prediction.neighbor_count)]);
    rows.push(["最近邻达标样本数", String(prediction.positive_count)]);
  }
  rows.forEach(([label, value]) => {
    const row = document.createElement("div");
    const labelNode = document.createElement("span");
    const valueNode = document.createElement("strong");
    labelNode.textContent = label;
    valueNode.textContent = value;
    row.append(labelNode, valueNode);
    target.appendChild(row);
  });
}

function drawStockChart(chart) {
  if (!chart?.closes?.length) return;
  const canvas = document.querySelector("#stock-chart");
  const ctx = prepareCanvas(canvas);
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const opens = chart.opens || chart.closes;
  const closes = chart.closes;
  const highs = chart.highs || closes;
  const lows = chart.lows || closes;
  const volumes = chart.volumes || closes.map(() => 0);
  const minPrice = Math.min(...lows);
  const maxPrice = Math.max(...highs);
  const maxVolume = Math.max(...volumes, 1);
  const pad = 36;
  const chartHeight = height * 0.68;
  const volumeTop = chartHeight + 28;
  const volumeHeight = height - volumeTop - 20;

  drawGrid(ctx, width, height, pad);
  drawCandles(ctx, opens, highs, lows, closes, minPrice, maxPrice, width, chartHeight, pad);

  const barWidth = Math.max(2, ((width - pad * 2) / volumes.length) * 0.55);
  volumes.forEach((value, index) => {
    const x = pad + (index / Math.max(volumes.length - 1, 1)) * (width - pad * 2) - barWidth / 2;
    const barHeight = normalize(value, 0, maxVolume) * volumeHeight;
    ctx.fillStyle = closes[index] >= opens[index] ? "rgba(7, 158, 107, 0.30)" : "rgba(217, 72, 95, 0.28)";
    ctx.fillRect(x, volumeTop + volumeHeight - barHeight, barWidth, barHeight);
  });

  ctx.fillStyle = "#6d7789";
  ctx.font = "12px sans-serif";
  ctx.fillText(`收盘 ${formatNumber(closes[closes.length - 1])}`, pad, 19);
}

function drawCandles(ctx, opens, highs, lows, closes, minPrice, maxPrice, width, chartHeight, pad) {
  const candleWidth = Math.max(2, ((width - pad * 2) / closes.length) * 0.58);
  closes.forEach((close, index) => {
    const open = opens[index];
    const high = highs[index];
    const low = lows[index];
    const x = xForIndex(index, width, pad, closes.length);
    const openY = yForPrice(open, minPrice, maxPrice, chartHeight, pad);
    const closeY = yForPrice(close, minPrice, maxPrice, chartHeight, pad);
    const highY = yForPrice(high, minPrice, maxPrice, chartHeight, pad);
    const lowY = yForPrice(low, minPrice, maxPrice, chartHeight, pad);
    const rising = close >= open;
    ctx.strokeStyle = rising ? "#079e6b" : "#d9485f";
    ctx.fillStyle = rising ? "rgba(7, 158, 107, 0.18)" : "rgba(217, 72, 95, 0.62)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, highY);
    ctx.lineTo(x, lowY);
    ctx.stroke();
    const bodyTop = Math.min(openY, closeY);
    const bodyHeight = Math.max(1.5, Math.abs(closeY - openY));
    ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
    ctx.strokeRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
  });
}

function xForIndex(index, width, pad, pointCount) {
  if (pointCount <= 1) return pad;
  return pad + (index / (pointCount - 1)) * (width - pad * 2);
}

function yForPrice(price, minClose, maxClose, chartHeight, pad) {
  return pad + (1 - normalize(price, minClose, maxClose)) * (chartHeight - pad);
}

function drawGrid(ctx, width, height, pad) {
  ctx.strokeStyle = "#e9edf3";
  ctx.lineWidth = 1;
  for (let index = 0; index < 5; index += 1) {
    const y = pad + index * ((height - pad * 2) / 4);
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(width - pad, y);
    ctx.stroke();
  }
}

function prepareCanvas(canvas) {
  const ratio = Math.max(window.devicePixelRatio || 1, 1);
  const width = Math.max(canvas.clientWidth, 1);
  const height = Math.max(canvas.clientHeight, 1);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return ctx;
}

function clearCanvas(canvas) {
  const ctx = prepareCanvas(canvas);
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
}

function normalize(value, min, max) {
  if (max === min) return 0.5;
  return (value - min) / (max - min);
}

function formatNumber(value) {
  return Number(value).toFixed(2);
}

function formatOptionalNumber(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "--";
}

function formatPercent(value) {
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function formatOptionalPercent(value) {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(2)}%` : "--";
}

function formatOptionalMultiple(value) {
  const number = optionalFiniteNumber(value);
  return number === null ? "--" : `${number.toFixed(2)}×`;
}

function formatPercentInterval(low, high) {
  const lowNumber = optionalFiniteNumber(low);
  const highNumber = optionalFiniteNumber(high);
  if (lowNumber === null || highNumber === null) return "--";
  return `${formatOptionalPercent(lowNumber)} – ${formatOptionalPercent(highNumber)}`;
}

function formatPercentagePointDelta(value) {
  const number = optionalFiniteNumber(value);
  if (number === null) return "--";
  const percentagePoints = number * 100;
  return `${percentagePoints > 0 ? "+" : ""}${percentagePoints.toFixed(2)} 个百分点`;
}

function formatCurrency(value) {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 2,
  }).format(number);
}

function formatSignedCurrency(value) {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  const formatted = formatCurrency(Math.abs(number));
  return `${number > 0 ? "+" : number < 0 ? "-" : ""}${formatted}`;
}

function formatAmount(value) {
  const number = Number(value);
  if (Math.abs(number) >= 100000000) return `${(number / 100000000).toFixed(2)}亿`;
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)}万`;
  return number.toFixed(2);
}

function parseOptionalNumber(value) {
  const cleaned = String(value || "").trim();
  if (!cleaned) return null;
  const number = Number(cleaned);
  return Number.isFinite(number) ? number : null;
}

function readError(payload, fallback) {
  if (typeof payload?.error === "string") return payload.error;
  if (payload?.error?.message) return payload.error.message;
  return fallback;
}

function isValidSymbol(value) {
  return /^\d{6}$/.test(value);
}

function setTradeDate() {
  const now = new Date();
  const date = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
  }).format(now);
  setText("#trade-date", date);
}

function updateGlobalTime(value) {
  if (!value) {
    const now = new Date();
    setText("#global-update-time", now.toLocaleString("zh-CN", { hour12: false }));
    return;
  }
  setText("#global-update-time", String(value));
}

function setText(selector, value) {
  const node = document.querySelector(selector);
  if (node) node.textContent = value ?? "--";
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2800);
}

function debounce(callback, delay) {
  let timer = null;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => callback(...args), delay);
  };
}
