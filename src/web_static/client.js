let clientConnection = null;
let clientBusy = false;
let clientReceivedAt = 0;
let clientLastAutoAttempt = 0;

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
    if (action === "clear") document.querySelector("#client-auto").checked = false;
    runClientAction(action);
  });
}
document.querySelector("#client-auto").addEventListener("change", (event) => {
  clientLastAutoAttempt = Date.now();
  if (event.target.checked) runClientAction("sync");
});

async function clientRequest(action = null) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(action ? `/api/client/${action}` : "/api/client", {
      ...(action ? {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Desktop-Token": clientConnection?.request_token || "" },
        body: "{}",
      } : {}),
      cache: "no-store", signal: controller.signal,
    });
    const payload = await response.json();
    if (payload.connection) {
      clientConnection = payload.connection;
      clientReceivedAt = Date.now();
    }
    if (!response.ok || !payload.ok) throw new Error(payload.error || "连接检查失败。");
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
    setClientError(clientConnection?.last_error?.message || "");
  } catch (error) {
    invalidateClientView(error);
  } finally {
    clientBusy = false;
    renderClientConnection();
  }
}

async function runClientAction(action) {
  if (clientBusy) return;
  clientBusy = true;
  setClientError("");
  renderClientConnection();
  try {
    if (!clientConnection?.request_token) await clientRequest();
    await clientRequest(action);
    setClientError(clientConnection?.last_error?.message || "");
  } catch (error) {
    invalidateClientView(error);
    document.querySelector("#client-auto").checked = false;
  } finally {
    clientBusy = false;
    renderClientConnection();
  }
}

function invalidateClientView(error) {
  if (clientConnection) clientConnection.stale = true;
  setClientError(error.name === "AbortError" ? "读取超时，旧快照已标记过期。" : error.message);
}

function setClientError(message) {
  setText("#client-error", message);
  document.querySelector("#client-error").classList.toggle("hidden", !message);
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
  document.querySelector("#client-clear").disabled = clientBusy || !snapshot;
  document.querySelector("#client-auto").disabled = clientBusy || !data?.capabilities.read_account;
  if (!data?.capabilities.read_account) document.querySelector("#client-auto").checked = false;
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
  const table = document.querySelector("#client-positions");
  table.replaceChildren();
  if (!snapshot || snapshot.positions.length === 0) {
    const row = table.insertRow();
    const cell = row.insertCell();
    cell.colSpan = 7;
    cell.textContent = snapshot ? "该快照持仓为零，已核对账户市值。" : "尚无经过校验的持仓快照。";
  } else {
    snapshot.positions.forEach((position) => {
      const row = table.insertRow();
      for (const value of [position.symbol, position.name, position.quantity, position.available,
        position.price, position.cost, money(position.market_value)]) {
        row.insertCell().textContent = String(value);
      }
    });
  }
  renderClientFreshness();
}

function renderClientFreshness() {
  const data = clientConnection;
  const snapshot = data?.snapshot;
  const captured = snapshot ? Date.parse(snapshot.captured_at) : NaN;
  const stale = data?.stale || !Number.isFinite(captured)
    || Date.now() - captured >= 60000 || Date.now() - clientReceivedAt >= 60000;
  setText("#client-freshness", !snapshot ? "尚未同步" : stale ? "已过期，仅供历史参考" : "最近读取有效，非实时行情");
  document.querySelector(".client-snapshot").classList.toggle("client-stale", Boolean(snapshot && stale));
}

window.setInterval(() => {
  if (currentPage !== "client" || document.hidden) return;
  renderClientFreshness();
  if (!clientBusy && document.querySelector("#client-auto").checked && Date.now() - clientLastAutoAttempt >= 30000) {
    clientLastAutoAttempt = Date.now();
    runClientAction("sync");
  }
}, 1000);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && currentPage === "client") loadClientConnection();
});

if (window.location.hash === "#client") navigateTo("client");
