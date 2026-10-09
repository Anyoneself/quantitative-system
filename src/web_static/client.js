let clientConnection = null;
let clientBusy = false;
let clientReceivedAt = 0;
let clientLastAutoAttempt = 0;
let clientLastRiskPoll = 0;
let clientRiskPollingFailed = false;
const clientStockButtons = [];

const clientStateNames = {
  READY_TO_READ: "可尝试读取账户",
  UNSUPPORTED_PLATFORM: "当前环境不支持",
  NOT_INSTALLED: "未找到客户端",
  NOT_RUNNING: "客户端未启动",
  ACCESSIBILITY_REQUIRED: "等待辅助功能授权",
  UNSUPPORTED_VERSION: "客户端版本待验证",
  MULTIPLE_CLIENTS: "多个客户端实例",
  CLIENT_LOCATION_REQUIRED: "客户端位置未验证",
  READ_FAILED: "暂时无法读取",
};

document.querySelector("#client-check").addEventListener("click", () => loadClientConnection());
for (const action of ["launch", "sync", "permission", "clear"]) {
  document.querySelector(`#client-${action}`).addEventListener("click", () => {
    if (action === "clear") {
      document.querySelector("#client-auto").checked = false;
      document.querySelector("#client-watchlist-auto").checked = false;
    }
    runClientAction(action);
  });
}
for (const selector of ["#client-auto", "#dashboard-client-auto"]) {
  document.querySelector(selector).addEventListener("change", (event) => {
    document.querySelector("#client-auto").checked = event.target.checked;
    document.querySelector("#dashboard-client-auto").checked = event.target.checked;
    clientLastAutoAttempt = Date.now();
    if (event.target.checked) {
      document.querySelector("#client-watchlist-auto").checked = false;
      return runClientAction("sync");
    }
  });
}
for (const selector of ["#client-public-history", "#dashboard-public-history"]) {
  document.querySelector(selector).addEventListener("change", (event) => {
    runClientAction(event.target.checked ? "history-enable" : "history-disable");
  });
}
document.querySelector("#client-sync-watchlist").addEventListener("click", () => runClientAction("sync-watchlist"));
document.querySelector("#client-watchlist-auto").addEventListener("change", (event) => {
  clientLastAutoAttempt = Date.now();
  if (event.target.checked) {
    document.querySelector("#client-auto").checked = false;
    return runClientAction("sync-watchlist");
  }
});
document.querySelector("#client-stock-refresh").addEventListener("click", () => loadClientConnection());
document.querySelector("#client-stock-picker").addEventListener("change", (event) => {
  const [source, symbol] = event.target.value.split(":");
  useClientStock(source, symbol);
});

async function clientRequest(action = null, body = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(action ? `/api/client/${action}` : "/api/client", {
      ...(action ? {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Desktop-Token": clientConnection?.request_token || "" },
        body: JSON.stringify(body),
      } : {}),
      cache: "no-store", signal: controller.signal,
    });
    const payload = await response.json();
    if (payload.connection) {
      clientConnection = payload.connection;
      clientReceivedAt = Date.now();
    }
    if (!response.ok || !payload.ok) {
      const error = new Error(payload.error || "连接检查失败。");
      error.hasConnection = Boolean(payload.connection);
      throw error;
    }
    return payload;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function loadClientConnection() {
  if (clientBusy) return;
  clientBusy = true;
  renderClientConnection();
  try {
    await clientRequest();
    clientRiskPollingFailed = false;
    setClientError("");
  } catch (error) {
    clientRiskPollingFailed = true;
    invalidateClientView(error);
  } finally {
    clientBusy = false;
    renderClientConnection();
  }
  if (!clientLastAutoAttempt && clientConnection) {
    clientLastAutoAttempt = Date.now();
    if (clientAccountAutoAllowed() && clientSnapshotStale("positions")) runClientAction("sync");
  }
}

async function runClientAction(action) {
  if (clientBusy) return;
  if (["sync", "sync-watchlist"].includes(action)) clientLastAutoAttempt = Date.now();
  clientBusy = true;
  setClientError("");
  renderClientConnection();
  try {
    if (!clientConnection?.request_token) await clientRequest();
    await clientRequest(action);
    clientRiskPollingFailed = false;
    setClientError("");
  } catch (error) {
    invalidateClientView(error);
    if (action !== "sync" || clientConnection?.last_error?.code !== "TRADE_PAGE_REQUIRED") {
      document.querySelector("#client-auto").checked = false;
    }
    document.querySelector("#client-watchlist-auto").checked = false;
  } finally {
    clientBusy = false;
    renderClientConnection();
  }
}

function invalidateClientView(error) {
  if (clientConnection && !error.hasConnection) {
    clientConnection.stale = true;
    clientConnection.watchlist_stale = true;
  }
  setClientError(error.name === "AbortError" ? "读取超时，旧快照已标记过期。" : error.message);
}

function setClientError(message) {
  setText("#client-error", message);
  document.querySelector("#client-error").classList.toggle("hidden", !message);
  if (currentPage === "trade" && typeof setTradeError === "function") setTradeError(message);
  if (currentPage === "automation" && typeof setAutomationError === "function") setAutomationError(message);
}

function renderClientConnection() {
  const data = clientConnection;
  const client = data?.client;
  const snapshot = data?.snapshot;
  setText("#client-state", clientBusy ? "正在检查客户端" : (clientStateNames[client?.code] || "尚未检查"));
  setText("#client-version", `客户端版本 ${client?.version || "--"}`);
  setText("#client-message", client?.message || "尚未获取连接状态。");
  setText("#client-launch-capability", data?.capabilities.launch ? "可用" : "不可用");
  setText("#client-read-capability", data?.capabilities.read_account ? "只读，需打开交易持仓页" : "等待客户端或权限");
  document.querySelector("#client-launch").disabled = clientBusy || !data?.capabilities.launch;
  document.querySelector("#client-sync").disabled = clientBusy || !data?.capabilities.read_account;
  document.querySelector("#client-check").disabled = clientBusy;
  document.querySelector("#client-stock-refresh").disabled = clientBusy;
  document.querySelector("#client-clear").disabled = clientBusy || (!snapshot && !data?.watchlist);
  document.querySelector("#client-auto").disabled = clientBusy || !data?.capabilities.read_account;
  document.querySelector("#dashboard-client-auto").checked = document.querySelector("#client-auto").checked;
  document.querySelector("#dashboard-client-auto").disabled = clientBusy || !data?.capabilities.read_account
    || document.querySelector("#dashboard-source").value === "ledger";
  for (const selector of ["#client-public-history", "#dashboard-public-history"]) {
    if (!clientBusy) document.querySelector(selector).checked = Boolean(data?.public_history_enabled);
    document.querySelector(selector).disabled = clientBusy || !data?.request_token
      || selector === "#dashboard-public-history" && document.querySelector("#dashboard-source").value === "ledger";
  }
  document.querySelector("#client-sync-watchlist").disabled = clientBusy || !data?.capabilities.watchlist_sync;
  document.querySelector("#client-watchlist-auto").disabled = clientBusy || !data?.capabilities.watchlist_sync;
  if (!data?.capabilities.watchlist_sync) document.querySelector("#client-watchlist-auto").checked = false;
  setText("#client-watchlist-capability", data?.capabilities.watchlist_sync ? "可读取选中分组" : "等待客户端或权限");
  setText("#client-account-error", data?.last_error?.message || "");
  setText("#client-watchlist-error", data?.watchlist_last_error?.message || "");
  const needsPermission = client?.code === "ACCESSIBILITY_REQUIRED";
  document.querySelector("#client-permission").classList.toggle("hidden", !needsPermission);
  document.querySelector("#client-permission").disabled = clientBusy;
  document.querySelector("#client-runtime").classList.toggle("hidden", !needsPermission);
  setText("#client-runtime", `系统设置 → 隐私与安全性 → 辅助功能。当前服务运行程序：${client?.runtime_app || client?.runtime || "--"}`);
  setText("#client-captured", snapshot
    ? `平安证券 · 读取时间 ${new Date(snapshot.captured_at).toLocaleString("zh-CN")}`
    : "尚未读取账户；未导入组合账本。");
  const money = (value) => value == null ? "--" : Number(value).toLocaleString("zh-CN", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
  for (const [id, key] of [["assets", "total_assets"], ["market-value", "market_value"],
    ["cash", "cash_balance"], ["available", "available_cash"], ["withdrawable", "withdrawable_cash"]]) {
    setText(`#client-${id}`, money(snapshot?.funds[key]));
  }
  const reconciliationNote = clientFundsReconciliationNote(snapshot);
  setText("#client-funds-reconciliation", reconciliationNote);
  document.querySelector("#client-funds-reconciliation").classList.toggle("hidden", !reconciliationNote);
  const table = document.querySelector("#client-positions");
  clientStockButtons.length = 0;
  table.replaceChildren();
  if (!snapshot || snapshot.positions.length === 0) {
    const row = table.insertRow();
    const cell = row.insertCell();
    cell.colSpan = 8;
    cell.textContent = snapshot ? "该快照持仓为零，已核对账户市值。" : "尚无经过校验的持仓快照。";
  } else {
    snapshot.positions.forEach((position) => {
      const row = table.insertRow();
      for (const value of [position.symbol, position.name, position.quantity, position.available,
        position.price, position.cost, money(position.market_value)]) {
        row.insertCell().textContent = String(value);
      }
      appendClientStockAction(row, "positions", position.symbol);
    });
  }
  const watchlist = data?.watchlist;
  setText("#client-watchlist-group", watchlist ? `${watchlist.group_name} · ${watchlist.declared_count} 只` : "尚未同步分组");
  setText("#client-watchlist-captured", watchlist
    ? `读取时间 ${new Date(watchlist.captured_at).toLocaleString("zh-CN")} · 当前选中分组`
    : "尚无经过数量校验的自选股快照");
  const stockTable = document.querySelector("#client-watchlist");
  stockTable.replaceChildren();
  if (!watchlist?.stocks.length) {
    const cell = stockTable.insertRow().insertCell();
    cell.colSpan = 3;
    cell.textContent = watchlist ? "该分组为零只，已核对客户端数量。" : "尚未同步自选股。";
  } else {
    watchlist.stocks.forEach((stock) => {
      const row = stockTable.insertRow();
      row.insertCell().textContent = stock.symbol;
      row.insertCell().textContent = stock.name;
      appendClientStockAction(row, "watchlist", stock.symbol);
    });
  }
  renderClientFreshness();
  if (typeof renderTrading === "function") renderTrading();
  if (typeof renderAutomation === "function") renderAutomation();
  if (currentPage === "overview" && typeof renderDashboardView === "function") renderDashboardView();
}

function clientFundsReconciliationNote(snapshot) {
  const cashBasis = snapshot?.funds_reconciliation?.scope === "client_display"
    ? snapshot.funds_reconciliation.cash_basis : null;
  const cashLabel = cashBasis === "cash_balance" ? "资金余额" : cashBasis === "available_cash" ? "可用金额" : "";
  return cashLabel
    ? `显示值核对：总资产 ≈ 持仓市值 + ${cashLabel}。${cashBasis === "available_cash"
      ? "资金余额按客户端原值保留，差异原因未核实。" : ""}非券商账本对账。` : "";
}

function clientSnapshotStale(source) {
  const data = clientConnection;
  const watchlist = source === "watchlist";
  const snapshot = watchlist ? data?.watchlist : data?.snapshot;
  const captured = snapshot ? Date.parse(snapshot.captured_at) : NaN;
  return Boolean((watchlist ? data?.watchlist_stale : data?.stale) || !Number.isFinite(captured)
    || Date.now() - captured >= 60000 || Date.now() - clientReceivedAt >= 60000);
}

function renderClientFreshness() {
  const snapshot = clientConnection?.snapshot;
  const stale = clientSnapshotStale("positions");
  setText("#client-freshness", !snapshot ? "尚未同步" : stale ? "已过期，仅供历史参考" : "最近读取有效，非实时行情");
  document.querySelector(".client-snapshot").classList.toggle("client-stale", Boolean(snapshot && stale));
  const watchlist = clientConnection?.watchlist;
  const watchlistStale = clientSnapshotStale("watchlist");
  setText("#client-watchlist-freshness", !watchlist ? "尚未同步" : watchlistStale ? "已过期，仅供历史参考" : "最近读取有效，非实时同步");
  document.querySelector(".client-watchlist-section").classList.toggle("client-stale", Boolean(watchlist && watchlistStale));
  clientStockButtons.forEach(({ button, source }) => {
    button.disabled = clientBusy || clientSnapshotStale(source);
  });
  renderClientStockPicker();
  if (currentPage === "overview" && typeof renderDashboardClientFreshness === "function") {
    renderDashboardClientFreshness();
  }
  if (currentPage === "trade" && typeof renderTradeFreshness === "function") renderTradeFreshness();
  if (currentPage === "automation" && typeof renderAutomation === "function") renderAutomation();
}

function appendClientStockAction(row, source, symbol) {
  const action = document.createElement("button");
  action.type = "button";
  action.className = "text-button client-stock-action";
  action.textContent = "带入分析";
  action.addEventListener("click", () => useClientStock(source, symbol));
  row.insertCell().append(action);
  clientStockButtons.push({ button: action, source });
}

function renderClientStockPicker() {
  const picker = document.querySelector("#client-stock-picker");
  const previous = picker.value;
  picker.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "选择已同步股票";
  picker.append(placeholder);
  let count = 0;
  for (const [source, label, stocks] of [
    ["watchlist", `自选 · ${clientConnection?.watchlist?.group_name || ""}`, clientConnection?.watchlist?.stocks],
    ["positions", "平安证券持仓", clientConnection?.snapshot?.positions],
  ]) {
    if (!stocks?.length) continue;
    const stale = clientSnapshotStale(source);
    const group = document.createElement("optgroup");
    group.label = `${label}${stale ? " · 已过期" : ""}`;
    group.disabled = stale;
    stocks.forEach((stock) => {
      const option = document.createElement("option");
      option.value = `${source}:${stock.symbol}`;
      option.textContent = `${stock.symbol} ${stock.name}`;
      group.append(option);
    });
    picker.append(group);
    if (!stale) count += stocks.length;
  }
  picker.value = previous;
  picker.disabled = clientBusy || count === 0;
}

function useClientStock(source, symbol) {
  if (!["positions", "watchlist"].includes(source)) return;
  if (clientBusy || clientSnapshotStale(source)) {
    showToast("快照已过期或正在更新，请先重新同步。");
    return;
  }
  const stocks = source === "positions" ? clientConnection?.snapshot?.positions : clientConnection?.watchlist?.stocks;
  const stock = stocks?.find((item) => item.symbol === symbol);
  if (!stock) return;
  document.querySelector("#symbol").value = stock.symbol;
  const useHolding = source === "positions" && Number(stock.cost) > 0;
  document.querySelector("#cost-price").value = useHolding ? String(stock.cost) : "";
  document.querySelector("#quantity").value = useHolding ? String(stock.quantity) : "";
  setText("#client-analysis-source", `${source === "positions" ? "持仓" : "自选"}快照 · ${stock.symbol} ${stock.name}`);
  navigateTo("market", "single");
  setMode("single");
  if (source === "positions" && !useHolding) showToast("持仓成本非正数，已仅带入证券代码。");
}

function clientAccountAutoAllowed() {
  return !document.hidden && ["settings", "overview"].includes(currentPage)
    && !(currentPage === "overview" && document.querySelector("#dashboard-source").value === "ledger")
    && clientConnection?.capabilities.read_account && document.querySelector("#client-auto").checked
    && !document.querySelector("#client-watchlist-auto").checked;
}

window.setInterval(() => {
  if (!["settings", "market", "overview", "trade", "automation"].includes(currentPage) || document.hidden) return;
  renderClientFreshness();
  if (!clientBusy && !clientRiskPollingFailed && clientConnection?.risk?.status === "ANALYZING"
      && ["settings", "overview"].includes(currentPage) && Date.now() - clientLastRiskPoll >= 2000) {
    clientLastRiskPoll = Date.now();
    loadClientConnection();
    return;
  }
  const action = currentPage === "settings" && document.querySelector("#client-watchlist-auto").checked ? "sync-watchlist"
    : clientAccountAutoAllowed() ? "sync" : null;
  if (!clientBusy && action && Date.now() - clientLastAutoAttempt >= 30000) {
    clientLastAutoAttempt = Date.now();
    runClientAction(action);
  }
}, 1000);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  if (currentPage === "overview") loadDashboard();
  else if (["settings", "market", "trade", "automation"].includes(currentPage)) loadClientConnection();
});
