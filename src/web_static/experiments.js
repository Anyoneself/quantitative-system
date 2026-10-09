let currentExperiment = null;
let experimentRequestVersion = 0;
let experimentOrderPage = 0;

const experimentForm = document.querySelector("#experiment-form");
const experimentHistory = document.querySelector("#experiment-history");

document.querySelector("#experiment-prev").addEventListener("click", () => {
  experimentOrderPage = Math.max(0, experimentOrderPage - 1);
  renderExperimentOrders();
});
document.querySelector("#experiment-next").addEventListener("click", () => {
  experimentOrderPage += 1;
  renderExperimentOrders();
});

document.querySelector("#experiment-source").addEventListener("change", (event) => {
  document.querySelector("#experiment-file-label").classList.toggle("hidden", event.target.value !== "file");
});

experimentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const version = ++experimentRequestVersion;
  setExperimentState("loading");
  document.querySelector("#experiment-submit").disabled = true;
  experimentHistory.disabled = true;
  try {
    const payload = {
      config: {
        strategy: { max_weight: readNumberInput("#experiment-weight") },
        simulation: {
          initial_cash: readNumberInput("#experiment-cash"),
          max_drawdown: readNumberInput("#experiment-drawdown"),
        },
      },
    };
    if (document.querySelector("#experiment-source").value === "demo") {
      payload.demo = true;
    } else {
      const file = document.querySelector("#experiment-file").files[0];
      if (!file) throw new Error("尚未选择历史数据快照。");
      if (file.size > 1_000_000) throw new Error("Web 快照上限为 1 MB；更大的快照需使用 research CLI。");
      payload.dataset = JSON.parse(await file.text());
    }
    const response = await requestJson("/api/experiments", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (version !== experimentRequestVersion) return;
    renderExperiment(response.experiment);
    await loadExperimentHistory(response.experiment.run_id);
  } catch (error) {
    if (version === experimentRequestVersion) {
      setExperimentState("error");
      setText("#experiment-error", error.message);
    }
  } finally {
    document.querySelector("#experiment-submit").disabled = false;
    experimentHistory.disabled = false;
  }
});

experimentHistory.addEventListener("change", async () => {
  if (!experimentHistory.value) return;
  const version = ++experimentRequestVersion;
  setExperimentState("loading");
  try {
    const response = await requestJson(`/api/experiments/${encodeURIComponent(experimentHistory.value)}`);
    if (version === experimentRequestVersion) renderExperiment(response.experiment);
  } catch (error) {
    if (version === experimentRequestVersion) {
      setExperimentState("error");
      setText("#experiment-error", error.message);
    }
  }
});

document.querySelector("#experiment-download").addEventListener("click", () => {
  if (!currentExperiment) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(currentExperiment, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${currentExperiment.run_id}.json`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
});

window.addEventListener("resize", debounce(() => {
  if (currentPage === "experiments") drawExperimentChart();
}, 120));

async function loadExperimentHistory(selectedId = null) {
  try {
    const response = await requestJson("/api/experiments");
    const previous = selectedId || experimentHistory.value;
    experimentHistory.replaceChildren(new Option("选择实验记录", ""));
    response.experiments.forEach((item) => {
      const text = item.error
        ? `${item.run_id} · ${item.error}`
        : `${item.created_at.slice(0, 19).replace("T", " ")} UTC · ${item.synthetic ? "合成演示" : "历史数据"}`;
      experimentHistory.add(new Option(text, item.run_id));
    });
    experimentHistory.value = previous;
  } catch (error) {
    setText("#experiment-error", error.message);
    document.querySelector("#experiment-error").classList.remove("hidden");
  }
}

function setExperimentState(state) {
  ["loading", "empty", "result", "error"].forEach((name) => {
    document.querySelector(`#experiment-${name}`).classList.toggle("hidden", state !== name);
  });
  document.querySelector("#experiment-download").disabled = state !== "result";
}

function renderExperiment(report) {
  currentExperiment = report;
  experimentOrderPage = 0;
  const result = report.out_of_sample;
  const metrics = result.summary;
  const passed = report.checks.filter((item) => item.passed).length;
  setText("#experiment-mode", report.synthetic ? "合成演示 · 非真实业绩 · 实盘禁用" : "历史数据 · 实盘禁用");
  setText("#experiment-status", `${report.synthetic ? "合成数据，非真实业绩" : "历史研究，非收益承诺"} · 检查 ${passed}/${report.checks.length} 通过 · 发布阻断`);
  setText("#experiment-id", report.run_id);
  setText("#experiment-return", formatOptionalPercent(metrics.total_return));
  setText("#experiment-max-dd", formatOptionalPercent(metrics.max_drawdown));
  setText("#experiment-sharpe", formatOptionalNumber(metrics.sharpe));
  setText("#experiment-stress", formatOptionalPercent(report.cost_stress.summary.total_return));
  setText("#experiment-period", `${result.equity_curve[0].date} 至 ${result.equity_curve.at(-1).date}`);
  setText("#experiment-halt", metrics.halted ? "已触发永久熔断" : "未触发永久熔断");
  setText("#experiment-trades", `${metrics.trade_count} 笔成交 · 费用 ${formatCurrency(metrics.fees)}`);
  setText("#experiment-benchmark", `基准毛收益 ${formatOptionalPercent(report.benchmark.total_return)}`);
  setText("#holdout-return", formatOptionalPercent(report.holdout?.summary.total_return));
  setText("#holdout-stress", formatOptionalPercent(report.holdout?.cost_stress.total_return));
  setText("#holdout-drawdown", formatOptionalPercent(report.holdout?.summary.max_drawdown));
  setText("#holdout-period", report.holdout
    ? `${report.holdout.start} 至 ${report.holdout.end} · 参数冻结 · 资金与风控状态连续`
    : "该历史报告不含最终留出期。");
  const percentChecks = new Set(["net_return", "benchmark", "drawdown", "cost_stress", "confidence", "positive_folds",
    "holdout_return", "holdout_stress", "holdout_confidence"]);
  const checks = document.querySelector("#experiment-checks");
  checks.replaceChildren();
  report.checks.forEach((item) => {
    const row = document.createElement("tr");
    const format = percentChecks.has(item.id) ? formatOptionalPercent : formatOptionalNumber;
    [item.name, item.passed ? "通过" : "未通过", format(item.value), format(item.threshold)].forEach((value) => appendCell(row, value));
    row.cells[1].className = item.passed ? "positive" : "negative";
    checks.append(row);
  });
  const folds = document.querySelector("#experiment-folds");
  folds.replaceChildren();
  report.folds.forEach((fold) => {
    const row = document.createElement("tr");
    [`${fold.train_start} / ${fold.train_end}`, `${fold.test_start} / ${fold.test_end}`,
      `${fold.selected_lookback} 日`, formatOptionalPercent(fold.oos.total_return),
      formatOptionalPercent(fold.oos.max_drawdown)].forEach((value) => appendCell(row, value));
    folds.append(row);
  });
  const eventNames = { DRAWDOWN_HALT: "回撤熔断", DAILY_LOSS_PAUSE: "单日亏损暂停", STALE_DATA_HALT: "行情陈旧熔断" };
  renderList("#experiment-events", result.risk_events.length
    ? result.risk_events.map((item) => `${item.date} · ${eventNames[item.kind] || item.kind}${item.value == null ? "" : ` · ${formatOptionalPercent(item.value)}`}`)
    : ["无触发事件"]);
  renderExperimentOrders();
  renderResearchAudit("experiment", report.data_audit);
  renderPnlAttribution("experiment", result.reconciliation);
  renderList("#experiment-limitations", report.limitations);
  setExperimentState("result");
  requestAnimationFrame(drawExperimentChart);
}

function renderResearchAudit(prefix, audit) {
  const statuses = { BLOCKED: "存在阻断项", REVIEW_REQUIRED: "待独立核验" };
  setText(`#${prefix}-audit-status`, statuses[audit?.status] || "尚无体检记录");
  const table = document.querySelector(`#${prefix}-data-checks`);
  table.replaceChildren();
  (audit?.checks || []).forEach((item) => {
    const row = table.insertRow();
    [item.name, item.passed ? "规则通过" : "未通过 / 未核验", item.detail].forEach((value) => appendCell(row, value));
    row.cells[1].className = item.passed ? "positive" : "negative";
  });
  if (!audit) return appendEmptyRow(table, 3, "旧记录尚无数据体检");
  const details = [
    ["行情来源", audit.synthetic ? "合成样本" : "提供方声明", audit.data_source || "旧记录未提供"],
    ["声明日历", audit.calendar_source || "无独立来源", audit.calendar_status],
    ["基准缺日", audit.missing_benchmark_dates.length, audit.missing_benchmark_dates.slice(0, 20).join("、") || "未检出"],
    ["非声明交易日", audit.unexpected_benchmark_dates.length, audit.unexpected_benchmark_dates.slice(0, 20).join("、") || "未检出"],
    ...audit.missing_member_bars.map((item) => [`${item.symbol} 行情缺日`, item.count, item.dates.join("、")]),
    ...audit.price_jumps.map((item) => [`${item.symbol} 开盘跳变`, item.date, formatOptionalPercent(item.open_gap)]),
  ];
  details.forEach((values) => {
    const row = table.insertRow();
    values.forEach((value) => appendCell(row, value));
  });
}

function renderPnlAttribution(prefix, reconciliation) {
  setText(`#${prefix}-reconciliation`, reconciliation?.status === "BALANCED" ? "独立对账平衡" : "尚未对账");
  const table = document.querySelector(`#${prefix}-attribution`);
  table.replaceChildren();
  (reconciliation?.attribution || []).forEach((item) => {
    const row = table.insertRow();
    [item.symbol, ...["realized_pnl", "unrealized_pnl", "net_pnl", "fees"].map((key) => formatCurrency(item[key]))]
      .forEach((value) => appendCell(row, value));
  });
  if (!table.rows.length) appendEmptyRow(table, 5, "暂无成交归因");
}

function renderExperimentOrders() {
  if (!currentExperiment) return;
  const allOrders = currentExperiment.out_of_sample.orders;
  const pages = Math.max(1, Math.ceil(allOrders.length / 20));
  experimentOrderPage = Math.min(experimentOrderPage, pages - 1);
  const orders = document.querySelector("#experiment-orders");
  orders.replaceChildren();
  allOrders.slice(experimentOrderPage * 20, (experimentOrderPage + 1) * 20).forEach((order) => {
    const row = document.createElement("tr");
    [order.signal_date, order.trade_date, order.symbol, order.side === "BUY" ? "买入" : "卖出",
      `${order.requested_quantity} / ${order.filled_quantity}`, formatOptionalNumber(order.price),
      formatCurrency(order.fee), order.reason].forEach((value) => appendCell(row, value));
    orders.append(row);
  });
  if (!allOrders.length) appendEmptyRow(orders, 8, "没有模拟订单");
  setText("#experiment-order-count", `${allOrders.length} 笔订单`);
  setText("#experiment-page-count", `${experimentOrderPage + 1} / ${pages}`);
  document.querySelector("#experiment-prev").disabled = experimentOrderPage === 0;
  document.querySelector("#experiment-next").disabled = experimentOrderPage >= pages - 1;
}

function drawExperimentChart() {
  if (!currentExperiment || currentPage !== "experiments") return;
  const report = currentExperiment;
  const initial = report.config.simulation.initial_cash;
  drawLineChart(document.querySelector("#experiment-chart"), report.out_of_sample.equity_curve.map((point, index) => ({
    date: point.date, strategy: point.equity / initial,
    benchmark: report.cost_stress.equity_curve[index].equity / initial,
  })));
}

if (window.location.hash === "#experiments") navigateTo("experiments");
