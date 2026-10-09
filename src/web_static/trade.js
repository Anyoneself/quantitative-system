const tradeForm = document.querySelector("#trade-form");
let tradeSide = "BUY";
let tradeDraft = null;
let tradeRequestVersion = 0;
let tradeReviewBusy = false;

function invalidateTradeDraft() {
  tradeRequestVersion++;
  tradeDraft = null;
  document.querySelector("#trade-draft").classList.add("hidden");
  document.querySelector("#trade-client-open").disabled = true;
  setText("#trade-status", "未提交");
}

function selectTradeSide(side) {
  tradeSide = side;
  invalidateTradeDraft();
  document.querySelectorAll("[data-trade-side]").forEach(tab => {
    const selected = tab.dataset.tradeSide === side;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected) tradeForm.setAttribute("aria-labelledby", tab.id);
  });
  document.querySelector("#trade-price").value = "";
  document.querySelector("#trade-quantity").value = "";
  setTradeError("");
  renderTradeFreshness();
}

for (const tab of document.querySelectorAll("[data-trade-side]")) {
  tab.addEventListener("click", () => selectTradeSide(tab.dataset.tradeSide));
  tab.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const side = event.key === "Home" ? "BUY" : event.key === "End" ? "SELL" : tradeSide === "BUY" ? "SELL" : "BUY";
    selectTradeSide(side);
    document.querySelector(side === "BUY" ? "#trade-buy" : "#trade-sell").focus();
  });
}
tradeForm.addEventListener("input", () => {
  invalidateTradeDraft();
  setTradeError("");
  renderTradeFreshness();
});
document.querySelector("#trade-stock-picker").addEventListener("change", event => {
  document.querySelector("#trade-symbol").value = event.target.value;
  document.querySelector("#trade-price").value = "";
  document.querySelector("#trade-quantity").value = "";
  invalidateTradeDraft();
  renderTradeFreshness();
});
document.querySelector("#trade-sync").addEventListener("click", () => {
  invalidateTradeDraft();
  runClientAction("sync");
});
document.querySelector("#trade-launch").addEventListener("click", () => runClientAction("launch"));

function setTradeError(message) {
  setText("#trade-error", message);
  document.querySelector("#trade-error").classList.toggle("hidden", !message);
}

tradeForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (clientBusy || tradeReviewBusy) return;
  invalidateTradeDraft();
  const version = tradeRequestVersion;
  const ticket = { side: tradeSide, symbol: document.querySelector("#trade-symbol").value.trim(),
    price: document.querySelector("#trade-price").value, quantity: Number(document.querySelector("#trade-quantity").value) };
  tradeReviewBusy = clientBusy = true;
  setTradeError("");
  renderClientConnection();
  try {
    if (!clientConnection?.request_token) await clientRequest();
    const payload = await clientRequest("prepare-trade", ticket);
    if (version !== tradeRequestVersion || currentPage !== "trade") return;
    const draft = payload.draft;
    if (!draft || draft.status !== "NOT_SUBMITTED" || draft.submitted !== false
        || draft.side !== ticket.side || draft.symbol !== ticket.symbol
        || Number(draft.price) !== Number(ticket.price) || draft.quantity !== ticket.quantity) {
      throw new Error("委托核对结果不一致，请重新核对。");
    }
    tradeDraft = draft;
    renderTradeDraft();
    setText("#trade-status", "资金与数量已核对，委托未提交");
  } catch (error) {
    if (version !== tradeRequestVersion || currentPage !== "trade") return;
    invalidateClientView(error);
    setTradeError(error.name === "AbortError" ? "核对超时，委托未提交，请重试。" : error.message);
    tradeDraft = null;
  } finally {
    tradeReviewBusy = clientBusy = false;
    renderClientConnection();
  }
});

document.querySelector("#trade-client-open").addEventListener("click", async () => {
  renderTradeFreshness();
  if (!tradeDraft || clientBusy) return;
  const version = tradeRequestVersion;
  clientBusy = true;
  renderClientConnection();
  try {
    await clientRequest("launch");
    if (version === tradeRequestVersion) setText("#trade-status", "客户端已打开；请手动填写并确认，系统未提交委托");
  } catch (error) {
    if (version === tradeRequestVersion) invalidateClientView(error);
  } finally {
    clientBusy = false;
    renderClientConnection();
  }
});

function renderTradeDraft() {
  const draft = tradeDraft;
  const list = document.querySelector("#trade-draft-fields");
  list.replaceChildren();
  for (const [label, value, copyValue] of [
    ["方向", draft.side === "BUY" ? "买入" : "卖出"],
    ["股票代码", draft.symbol, draft.symbol],
    ["限价", formatOptionalNumber(draft.price), draft.price],
    ["数量", `${draft.quantity} 股`, String(draft.quantity)],
    ["委托金额", formatOptionalNumber(draft.amount)],
    ...(draft.fee_reserve ? [["手续费预留（非实际费用）", formatOptionalNumber(draft.fee_reserve)]] : []),
  ]) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const valueNode = document.createElement("dd");
    term.textContent = label;
    valueNode.textContent = value;
    row.append(term, valueNode);
    if (copyValue !== undefined) {
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "icon-button";
      copy.title = `复制${label}`;
      copy.setAttribute("aria-label", copy.title);
      copy.textContent = "⧉";
      copy.addEventListener("click", async () => {
        renderTradeFreshness();
        if (tradeDraft !== draft) return;
        try { await navigator.clipboard.writeText(copyValue); showToast(`${label}已复制`); }
        catch { setTradeError("无法访问剪贴板，请手动输入客户端。"); }
      });
      row.append(copy);
    }
    list.append(row);
  }
  setText("#trade-draft-time", `核对时间：${new Date(draft.checked_at).toLocaleString("zh-CN")} · 60秒内有效 · 账户编号未核验`);
  document.querySelector("#trade-draft").classList.remove("hidden");
}

function renderTrading() {
  const data = clientConnection;
  const snapshot = data?.snapshot;
  setText("#trade-cash", formatOptionalNumber(snapshot?.funds.available_cash));
  setText("#trade-market-value", formatOptionalNumber(snapshot?.funds.market_value));
  setText("#trade-captured", snapshot ? new Date(snapshot.captured_at).toLocaleString("zh-CN") : "--");
  document.querySelector("#trade-launch").disabled = clientBusy || !data?.capabilities.launch;
  document.querySelector("#trade-sync").disabled = clientBusy || !data?.capabilities.read_account;
  document.querySelector("#trade-review").disabled = clientBusy || !data?.capabilities.read_account;
  setText("#trade-review", tradeReviewBusy ? "正在核对..." : "核对委托");
  const picker = document.querySelector("#trade-stock-picker");
  const previous = picker.value;
  picker.replaceChildren(new Option("手动输入股票代码", ""));
  const seen = new Set();
  for (const stock of [...(snapshot?.positions || []), ...(data?.watchlist?.stocks || [])]) {
    if (seen.has(stock.symbol)) continue;
    seen.add(stock.symbol);
    picker.append(new Option(`${stock.symbol} ${stock.name}`, stock.symbol));
  }
  picker.value = previous;
  const tbody = document.querySelector("#trade-positions");
  tbody.replaceChildren();
  for (const position of snapshot?.positions || []) {
    const row = tbody.insertRow();
    for (const value of [`${position.name} ${position.symbol}`, position.quantity, position.available,
      formatOptionalNumber(position.price)]) row.insertCell().textContent = value;
    const sell = document.createElement("button");
    sell.type = "button";
    sell.className = "text-button";
    sell.textContent = "准备卖出";
    sell.setAttribute("aria-label", `准备卖出 ${position.name}`);
    sell.dataset.available = position.available;
    sell.addEventListener("click", () => {
      selectTradeSide("SELL");
      document.querySelector("#trade-symbol").value = position.symbol;
      picker.value = position.symbol;
      renderTradeFreshness();
      document.querySelector("#trade-price").focus();
    });
    row.insertCell().append(sell);
  }
  if (!snapshot?.positions.length) {
    const cell = tbody.insertRow().insertCell();
    cell.colSpan = 5;
    cell.textContent = snapshot ? "该快照暂无持仓" : "尚未同步持仓";
  }
  renderTradeFreshness();
}

function renderTradeFreshness() {
  const stale = clientSnapshotStale("positions");
  const data = clientConnection;
  setText("#trade-account-state", !data?.snapshot ? "尚未同步" : stale ? "快照过期，须重新核对" : "快照有效，非实时");
  setText("#trade-connection-message", data?.last_error?.message || (!stale
    ? "账户页面读取成功，账户编号未核验。" : data?.client.message || "尚未获取连接状态。"));
  document.querySelectorAll("#trade-positions button").forEach(button => {
    button.disabled = stale || clientBusy || Number(button.dataset.available) <= 0;
  });
  if (tradeDraft && (stale || !Number.isFinite(Date.parse(tradeDraft.expires_at))
      || Date.now() >= Date.parse(tradeDraft.expires_at) || tradeDraft.snapshot_id !== data?.snapshot?.snapshot_id)) {
    invalidateTradeDraft();
    setText("#trade-status", "核对已失效，委托未提交，请重新核对");
  }
  document.querySelector("#trade-client-open").disabled = !tradeDraft || clientBusy || !data?.capabilities.launch;
  const symbol = document.querySelector("#trade-symbol").value.trim();
  const available = data?.snapshot?.positions.find(item => item.symbol === symbol)?.available || 0;
  setText("#trade-availability", tradeSide === "BUY" ? "主板买入：100股整数倍；手续费以客户端为准。"
    : stale ? "可卖数量待重新核对" : `客户端快照可卖 ${available} 股；当日新买入或冻结股份不计入可卖数量。`);
  const price = Number(document.querySelector("#trade-price").value);
  const quantity = Number(document.querySelector("#trade-quantity").value);
  setText("#trade-amount", price > 0 && Number.isInteger(quantity) && quantity > 0
    ? formatOptionalNumber(Math.round(price * 100) * quantity / 100) : "--");
}

renderTrading();
