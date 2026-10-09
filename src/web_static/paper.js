let paperSession = null;
let paperToken = null;
let paperBusy = false;
let paperVersion = 0;
let paperSelectedId = "";
let paperRecoveryAvailable = false;
let paperUncertain = false;
const paperHistory = document.querySelector("#paper-history");

document.querySelector("#paper-source").addEventListener("change", (event) => {
  const file = event.target.value === "file";
  document.querySelector("#paper-symbols-label").classList.toggle("hidden", file);
  document.querySelector("#paper-file-label").classList.toggle("hidden", !file);
});

document.querySelector("#paper-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (paperBusy) return;
  try {
    const payload = { config: {
      strategy: { max_weight: readNumberInput("#paper-weight") },
      simulation: { initial_cash: readNumberInput("#paper-cash"), max_drawdown: readNumberInput("#paper-drawdown") },
    } };
    if (document.querySelector("#paper-source").value === "file") {
      const file = document.querySelector("#paper-file").files[0];
      if (!file || file.size > 900000) throw new Error("请选择不超过 900 KB 的行情快照。");
      payload.dataset = JSON.parse(await file.text());
    } else {
      payload.symbols = document.querySelector("#paper-symbols").value.split(/[,，\s]+/).filter(Boolean);
      if (!payload.symbols.length) throw new Error("尚未填写证券代码。");
    }
    await mutatePaper("/api/paper", payload);
  } catch (error) { paperError(error.message); }
});

document.querySelector("#paper-refresh").addEventListener("click", () => loadPaperSessions());
document.querySelector("#paper-download").addEventListener("click", () => {
  if (!paperSession || paperUncertain || paperBusy) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(paperSession, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `paper-${paperSession.session_id}-r${paperSession.storage_revision ?? 0}.json`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
});
document.querySelector("#paper-advance").addEventListener("click", () => runPaperAction("advance"));
document.querySelector("#paper-pause").addEventListener("click", () => {
  if (window.confirm("暂停此模拟的后续推进？已有账本将保留。")) runPaperAction("pause");
});
document.querySelector("#paper-resume").addEventListener("click", () => {
  if (window.confirm("继续观察冻结策略？中断将永久标记为证据缺口，既有熔断不会解除。")) runPaperAction("resume");
});
document.querySelector("#paper-recover").addEventListener("click", () => {
  if (window.confirm("仅恢复最新完整存档并保持暂停？最新存档损坏时将拒绝恢复，不会回退到较早版本。")) runPaperAction("recover");
});
paperHistory.addEventListener("change", () => {
  document.querySelector("#paper-auto").checked = false;
  loadPaperSessions(paperHistory.value);
});

function paperError(message) {
  setText("#paper-error", message);
  document.querySelector("#paper-error").classList.remove("hidden");
  document.querySelector("#paper-auto").checked = false;
}

function setPaperBusy(value) {
  paperBusy = value;
  document.querySelector("#paper-loading").classList.toggle("hidden", !value);
  ["#paper-create", "#paper-refresh", "#paper-history"].forEach((selector) => {
    document.querySelector(selector).disabled = value || (selector === "#paper-create" && !paperToken);
  });
  ["#paper-advance", "#paper-pause", "#paper-auto"].forEach((selector) => {
    document.querySelector(selector).disabled = value || paperUncertain || !paperSession || paperSession.status === "PAUSED"
      || !paperToken || (selector !== "#paper-pause" && paperSession.operations?.code_changed);
  });
  document.querySelector("#paper-resume").disabled = value || paperUncertain || !paperToken
    || paperSession?.status !== "PAUSED" || paperSession?.operations?.code_changed;
  document.querySelector("#paper-recover").classList.toggle("hidden", !paperRecoveryAvailable);
  document.querySelector("#paper-recover").disabled = value || !paperToken || !paperSelectedId || !paperRecoveryAvailable;
  document.querySelector("#paper-download").disabled = value || !paperSession || paperUncertain;
}

async function paperRequest(url, options = {}) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(180000) });
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error || "模拟请求失败。");
  return payload;
}

async function loadPaperSessions(selectedId = null) {
  if (paperBusy) return;
  const version = ++paperVersion;
  setPaperBusy(true);
  paperToken = null;
  paperRecoveryAvailable = false;
  const selected = selectedId ?? paperHistory.value;
  paperSession = null;
  paperSelectedId = "";
  document.querySelector("#paper-result").classList.add("hidden");
  document.querySelector("#paper-error").classList.add("hidden");
  try {
    const response = await paperRequest("/api/paper");
    if (version !== paperVersion) return;
    paperToken = response.request_token;
    paperHistory.replaceChildren(new Option("选择模拟记录", ""));
    response.sessions.forEach((session) => paperHistory.add(new Option(
      session.busy ? `${session.session_id} · 正在更新`
        : session.error ? `${session.session_id} · ${session.error}`
          : `${session.created_at.slice(0, 16)} · ${session.last_date} · ${session.status}`,
      session.session_id,
    )));
    paperHistory.value = selected;
    const entry = response.sessions.find((item) => item.session_id === selected);
    if (entry) {
      paperSelectedId = entry.session_id;
      paperRecoveryAvailable = Boolean(entry.error);
      if (entry.busy) throw new Error("该模拟正在更新，请稍后刷新。");
      const detail = await paperRequest(`/api/paper/${encodeURIComponent(paperSelectedId)}`);
      if (version === paperVersion) renderPaperSession(detail.session);
    } else {
      paperSession = null;
      document.querySelector("#paper-result").classList.add("hidden");
      document.querySelector("#paper-empty").classList.remove("hidden");
    }
  } catch (error) {
    document.querySelector("#paper-empty").classList.add("hidden");
    paperError(error.message);
  } finally { setPaperBusy(false); }
}

async function mutatePaper(url, payload) {
  if (paperBusy || !paperToken) return;
  setPaperBusy(true);
  document.querySelector("#paper-error").classList.add("hidden");
  let selected;
  try {
    const response = await paperRequest(url, { method: "POST",
      headers: { "Content-Type": "application/json", "X-Desktop-Token": paperToken },
      body: JSON.stringify(payload),
    });
    renderPaperSession(response.session);
    selected = response.session.session_id;
  } catch (error) {
    paperUncertain = true;
    if (paperSession) setText("#paper-status", "状态待核验 · 上次读取");
    paperError(`${error.message} 请检查状态后再操作。`);
  }
  finally { setPaperBusy(false); }
  if (selected) await loadPaperSessions(selected);
}

async function runPaperAction(action) {
  const id = action === "recover" ? paperSelectedId : paperSession?.session_id;
  const payload = {};
  if (paperSession && action !== "recover") payload.expected_revision = paperSession.storage_revision ?? 0;
  if (id) await mutatePaper(`/api/paper/${encodeURIComponent(id)}/${action}`, payload);
}

function renderPaperSession(session) {
  paperSession = session;
  paperSelectedId = session.session_id;
  paperRecoveryAvailable = false;
  paperUncertain = false;
  const metrics = session.result.summary;
  setText("#paper-status", { ACTIVE: "已登记 · 可推进", HALTED: "已熔断 · 仅尝试模拟退出", PAUSED: "已暂停" }[session.status] || session.status);
  setText("#paper-id", session.session_id);
  setText("#paper-equity", formatCurrency(metrics.final_equity));
  setText("#paper-return", formatOptionalPercent(metrics.total_return));
  setText("#paper-dd", formatOptionalPercent(metrics.max_drawdown));
  setText("#paper-date", `估值日期 ${session.last_date}`);
  setText("#paper-fees", `${metrics.trade_count} 笔成交 · 费用 ${formatCurrency(metrics.fees)}`);
  setText("#paper-halt", metrics.halted ? "已触发永久熔断" : "未触发永久熔断");
  setText("#paper-days", `${session.qualified_days} / 126`);
  setText("#paper-evidence", session.has_replay_gap ? "存在补算或中断 · 证据受限" : "前向观察 · 尚非盈利证明");
  setText("#paper-signal-date", `${session.next_intent.signal_date} · ${session.next_intent.rebalance ? "调仓" : "维持仓位"}`);
  const intents = document.querySelector("#paper-intents");
  intents.replaceChildren();
  Object.entries(session.next_intent.weights).forEach(([symbol, weight]) => {
    const row = intents.insertRow();
    [symbol, formatOptionalPercent(weight)].forEach((value) => appendCell(row, value));
  });
  if (!intents.rows.length) appendEmptyRow(intents, 2, "无目标仓位");
  const orders = document.querySelector("#paper-orders");
  orders.replaceChildren();
  session.result.orders.slice(-30).reverse().forEach((order) => {
    const row = orders.insertRow();
    [order.trade_date, order.symbol, order.side === "BUY" ? "买入" : "卖出", order.filled_quantity,
      formatCurrency(order.fee), order.reason].forEach((value) => appendCell(row, value));
  });
  if (!orders.rows.length) appendEmptyRow(orders, 6, "尚无模拟成交");
  const observations = document.querySelector("#paper-observations");
  observations.replaceChildren();
  session.observations.slice(-30).reverse().forEach((item) => {
    const row = observations.insertRow();
    const resolution = { FROZEN_SIGNAL: "按冻结信号", NO_REBALANCE: "维持仓位", RISK_REDUCTION: "风控优先减仓" };
    [item.date, item.observed_at, item.mode === "FORWARD" ? "前向观察" : "历史补算",
      resolution[item.intent_resolution] || "旧记录未区分"].forEach((value) => appendCell(row, value));
  });
  if (!observations.rows.length) appendEmptyRow(observations, 4, "等待下一交易日");
  const operations = session.operations;
  const workerStates = { NOT_STARTED: "未启动", STOPPED: "已退出", HEARTBEAT_EXPIRED: "心跳超时",
    INVALID_HEARTBEAT: "心跳无效", WAITING: "等待", UPDATING: "正在更新", ERROR: "更新失败" };
  setText("#paper-worker", `${operations?.worker.online ? "在线" : "离线"} · ${
    workerStates[operations?.worker.status] || "无记录"}`);
  setText("#paper-heartbeat", operations?.worker.heartbeat_at || "尚无心跳");
  setText("#paper-revision", `存档版本 ${operations?.revision ?? 0}`);
  const alerts = new Map([...(operations?.alerts || []), ...(session.monitoring?.alerts || [])]
    .map((item) => [item.code, item.message]));
  renderList("#paper-alerts", alerts.size ? [...alerts.values()] : ["暂无运行告警"]);
  renderPaperMonitoring(session.monitoring);
  renderResearchAudit("paper", session.data_audit);
  renderPnlAttribution("paper", session.result.reconciliation);
  const revisions = document.querySelector("#paper-revisions");
  const eventNames = { CREATE: "登记", ADVANCE: "推进", PAUSE: "暂停", RESUME: "继续观察", RECOVER: "恢复",
    OBSERVATION_FAILED: "观察失败", LEGACY_IMPORT: "旧账本迁移" };
  revisions.replaceChildren();
  (operations?.revisions || []).slice().reverse().forEach((item) => {
    const row = revisions.insertRow();
    [item.revision, eventNames[item.event] || item.event, item.saved_at, item.sha256.slice(0, 16)]
      .forEach((value) => appendCell(row, value));
    row.cells[3].title = item.sha256;
  });
  if (!revisions.rows.length) appendEmptyRow(revisions, 4, "旧账本尚未建立版本链");
  renderList("#paper-limitations", session.limitations);
  document.querySelector("#paper-result").classList.remove("hidden");
  document.querySelector("#paper-empty").classList.add("hidden");
  if (session.status === "PAUSED") document.querySelector("#paper-auto").checked = false;
  if (session.last_error) paperError(session.last_error);
  requestAnimationFrame(drawPaperChart);
}

function renderPaperMonitoring(report) {
  setText("#paper-benchmark-name", report ? `${report.benchmark.symbol} 价格净值` : "基准未提供");
  setText("#paper-benchmark-return", formatOptionalPercent(report?.benchmark.total_return));
  setText("#paper-excess", formatOptionalPercent(report?.relative.excess_return));
  setText("#paper-recent-excess", `近 20 日超额 ${formatOptionalPercent(report?.relative.recent_20_day_excess)}`);
  setText("#paper-tracking-error", formatOptionalPercent(report?.relative.tracking_error));
  setText("#paper-information-ratio", `信息比率 ${formatOptionalNumber(report?.relative.information_ratio)}`);
  setText("#paper-exposure", formatOptionalPercent(report?.portfolio.gross_exposure));
  setText("#paper-cash", report ? `可用模拟现金 ${formatCurrency(report.portfolio.cash)}` : "--");
  setText("#paper-fill-rate", formatOptionalPercent(report?.execution.fill_rate));
  setText("#paper-turnover", formatOptionalPercent(report?.execution.turnover_on_initial_cash));
  setText("#paper-shortfall", report ? formatCurrency(report.execution.modeled_opening_shortfall) : "--");
  setText("#paper-execution-counts", report
    ? `${report.execution.orders} 笔订单 · ${report.execution.filled_orders} 笔有成交 · ${report.execution.partial_orders} 笔部分成交 · ${report.execution.rejected_orders} 笔拒单`
    : "尚无执行体检");
  const statuses = { INSUFFICIENT: "观察日数不足", FAILED_CHECKS: "检查未通过", REVIEW_REQUIRED: "待人工复核 · 非实盘许可" };
  setText("#paper-check-status", statuses[report?.evidence.status] || "尚无体检");
  const checks = document.querySelector("#paper-checks");
  checks.replaceChildren();
  const percentChecks = new Set(["net_return", "benchmark", "net_ci", "active_ci"]);
  (report?.evidence.checks || []).forEach((item) => {
    const row = checks.insertRow();
    const format = percentChecks.has(item.id) ? formatOptionalPercent : formatOptionalNumber;
    [item.name, item.passed === null ? "样本不足" : item.passed ? "通过" : "未通过",
      format(item.value), format(item.threshold)].forEach((value) => appendCell(row, value));
    row.cells[1].className = item.passed ? "positive" : item.passed === false ? "negative" : "";
  });
  if (!checks.rows.length) appendEmptyRow(checks, 4, "尚无前向证据检查");
  const positions = document.querySelector("#paper-positions");
  positions.replaceChildren();
  (report?.portfolio.positions || []).forEach((item) => {
    const row = positions.insertRow();
    [item.symbol, item.quantity, formatOptionalPercent(item.weight), formatOptionalPercent(item.target_weight),
      formatOptionalNumber(item.price), item.mark_date, item.stale ? "陈旧估值" : "当日估值"]
      .forEach((value) => appendCell(row, value));
    if (item.stale) row.cells[6].className = "negative";
  });
  if (!positions.rows.length) appendEmptyRow(positions, 7, "暂无模拟持仓");
  const reasons = document.querySelector("#paper-rejections");
  reasons.replaceChildren();
  (report?.execution.reasons || []).forEach((item) => {
    const row = reasons.insertRow();
    [item.reason, item.count].forEach((value) => appendCell(row, value));
  });
  if (!reasons.rows.length) appendEmptyRow(reasons, 2, "无拒单或部分成交");
  renderList("#paper-monitor-notes", report?.limitations || ["尚无体检口径"]);
}

function drawPaperChart() {
  if (!paperSession || currentPage !== "paper") return;
  const canvas = document.querySelector("#paper-chart");
  const points = paperSession.monitoring?.comparison_curve || [];
  if (!points.length) {
    prepareCanvas(canvas);
    return;
  }
  drawLineChart(canvas, points);
}

window.addEventListener("resize", debounce(drawPaperChart, 120));
window.setInterval(() => {
  if (currentPage === "paper" && !document.hidden && !paperBusy
      && document.querySelector("#paper-auto").checked && paperSession?.status !== "PAUSED") runPaperAction("advance");
}, 3600000);
window.setInterval(() => {
  if (currentPage === "paper" && !document.hidden && !paperBusy && paperSelectedId) loadPaperSessions(paperSelectedId);
}, 60000);
if (window.location.hash === "#paper") navigateTo("paper");
