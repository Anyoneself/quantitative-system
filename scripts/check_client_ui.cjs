const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// 使用最小 DOM 替身测试状态变化，不连接真实账户或启动浏览器。
function element(tagName = "") {
  const classes = new Set();
  return {
    tagName, textContent: "", value: "", disabled: false, checked: false, children: [], listeners: {},
    classList: {
      toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
    addEventListener(name, listener) { this.listeners[name] = listener; },
    replaceChildren() { this.children = []; },
    append(...items) { this.children.push(...items); },
    insertRow() { const row = element(); this.children.push(row); return row; },
    insertCell() { const cell = element(); this.children.push(cell); return cell; },
  };
}

async function main() {
  const nodes = new Map();
  const query = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  };
  let nextResponse;
  let requested;
  let interval;
  let navigation;
  const context = vm.createContext({
    console, AbortController, Date, setTimeout, clearTimeout, currentPage: "client",
    document: { hidden: false, querySelector: query, createElement: element, addEventListener() {} },
    window: { location: { hash: "" }, setTimeout, clearTimeout, setInterval(fn) { interval = fn; } },
    setText: (selector, value) => { query(selector).textContent = value; },
    navigateTo: (page) => { navigation = page; },
    setMode() {}, showToast() {},
    fetch: async (url, options) => {
      requested = { url, options };
      if (nextResponse instanceof Error) throw nextResponse;
      return { ok: nextResponse.ok, json: async () => nextResponse };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../src/web_static/client.js"), "utf8"), context);
  const state = {
    client: { code: "READY_TO_READ", version: "5.3.5", message: "可读取" },
    request_token: "test-only", stale: false,
    capabilities: { launch: true, read_account: true, watchlist_sync: true, live_trading: false },
    watchlist_stale: false,
    watchlist: {
      captured_at: new Date().toISOString(), group_name: "自选股", declared_count: 2,
      scope: "selected_group", stocks: [{ symbol: "000001", name: "测试证券" }, { symbol: "688001", name: "测试科创" }],
    },
    snapshot: {
      captured_at: new Date().toISOString(),
      funds: { total_assets: "10000.00", market_value: "0.00", cash_balance: "10000.00",
        available_cash: "9000.00", withdrawable_cash: "8000.00" },
      positions: [],
    },
  };

  // Given 已有有效账户 When 同步 Then 展示金额并使用同源操作令牌。
  nextResponse = { ok: true, connection: structuredClone(state) };
  await vm.runInContext("loadClientConnection()", context);
  await vm.runInContext('runClientAction("sync")', context);
  assert.equal(query("#client-assets").textContent, "10,000.00");
  assert.equal(requested.url, "/api/client/sync");
  assert.equal(requested.options.headers["X-Desktop-Token"], "test-only");
  assert.equal(query("#client-sync").disabled, false);
  assert.match(query("#client-freshness").textContent, /非实时行情/);

  // Given 两种显示核对口径 When 展示快照 Then 说明匹配字段且不覆盖原金额。
  for (const basis of ["cash_balance", "available_cash"]) {
    const reconciled = structuredClone(state);
    reconciled.snapshot.funds_reconciliation = { scope: "client_display", cash_basis: basis };
    if (basis === "available_cash") reconciled.snapshot.funds.total_assets = "9000.00";
    nextResponse = { ok: true, connection: reconciled };
    await vm.runInContext("loadClientConnection()", context);
    assert.equal(query("#client-cash").textContent, "10,000.00");
    assert.equal(query("#client-available").textContent, "9,000.00");
    assert.equal(query("#client-assets").textContent, basis === "cash_balance" ? "10,000.00" : "9,000.00");
    assert.match(query("#client-funds-reconciliation").textContent, /显示值核对/);
    assert.match(query("#client-funds-reconciliation").textContent, /非券商账本对账/);
    if (basis === "available_cash") {
      assert.match(query("#client-funds-reconciliation").textContent, /持仓市值.*可用金额/);
      assert.match(query("#client-funds-reconciliation").textContent, /原值保留.*原因未核实/);
    } else {
      assert.match(query("#client-funds-reconciliation").textContent, /持仓市值.*资金余额/);
    }
    assert.equal(query("#client-funds-reconciliation").classList.contains("hidden"), false);
  }
  // Given 旧版或无快照 When 刷新 Then 不残留先前的核对说明。
  for (const snapshot of [structuredClone(state.snapshot), null]) {
    nextResponse = { ok: true, connection: { ...structuredClone(state), snapshot } };
    await vm.runInContext("loadClientConnection()", context);
    assert.equal(query("#client-funds-reconciliation").textContent, "");
    assert.equal(query("#client-funds-reconciliation").classList.contains("hidden"), true);
  }
  nextResponse = { ok: true, connection: structuredClone(state) };
  await vm.runInContext("loadClientConnection()", context);

  // Given 自选与持仓分别同步 When 展示 Then 不将股票池伪造成持仓。
  await vm.runInContext('runClientAction("sync-watchlist")', context);
  assert.equal(requested.url, "/api/client/sync-watchlist");
  assert.equal(query("#client-watchlist").children.length, 2);
  assert.match(query("#client-watchlist-group").textContent, /自选股.*2/);
  assert.equal(query("#client-watchlist").children[0].children[0].textContent, "000001");

  // Given 先前填过其他持仓 When 从自选带入分析 Then 清除无关成本且不自动请求行情。
  query("#cost-price").value = "10";
  query("#quantity").value = "100";
  requested = null;
  query("#client-watchlist").children[0].children[2].children[0].listeners.click();
  assert.equal(navigation, "market");
  assert.equal(query("#symbol").value, "000001");
  assert.equal(query("#cost-price").value, "");
  assert.equal(query("#quantity").value, "");
  assert.equal(requested, null);

  // Given 持仓通道错误 When 自选仍有效 Then 两类数据的过期状态相互独立。
  nextResponse = { ok: false, error: "请登录交易账户", connection: {
    ...structuredClone(state), snapshot: null, stale: true,
    last_error: { code: "TRADE_PAGE_REQUIRED", message: "请登录交易账户" },
  } };
  await vm.runInContext('runClientAction("sync")', context);
  assert.doesNotMatch(query("#client-watchlist-freshness").textContent, /过期/);
  assert.equal(query("#client-watchlist").children[0].children[2].children[0].disabled, false);
  nextResponse = { ok: true, connection: structuredClone(state) };
  await vm.runInContext("loadClientConnection()", context);

  // Given 网络断开 When 再次同步 Then 保留旧金额、标记过期并停止自动同步。
  query("#client-auto").checked = true;
  nextResponse = new Error("网络中断");
  await vm.runInContext('runClientAction("sync")', context);
  assert.equal(query("#client-assets").textContent, "10,000.00");
  assert.match(query("#client-freshness").textContent, /过期/);
  assert.equal(query("#client-auto").checked, false);
  assert.equal(query("#client-error").textContent, "网络中断");

  // Given 未授予权限 When 检查连接 Then 禁止同步且不显示伪造的空仓。
  nextResponse = { ok: true, connection: {
    ...state, client: { ...state.client, code: "ACCESSIBILITY_REQUIRED" },
    capabilities: { launch: true, read_account: false }, snapshot: null, stale: true,
  } };
  await vm.runInContext("loadClientConnection()", context);
  assert.equal(query("#client-sync").disabled, true);
  assert.equal(query("#client-sync-watchlist").disabled, true);
  assert.equal(query("#client-assets").textContent, "--");
  assert.equal(query("#client-permission").classList.contains("hidden"), false);
  assert.match(query("#client-positions").children[0].children[0].textContent, /尚无/);

  // Given 页面隐藏或离开连接页 When 到达自动读取周期 Then 不发起读取。
  query("#client-auto").checked = true;
  context.document.hidden = true;
  requested = null;
  interval();
  assert.equal(requested, null);
  context.document.hidden = false;
  context.currentPage = "market";
  interval();
  assert.equal(requested, null);

  // Given 股票名称包含 HTML When 渲染 Then 只写入文本。
  const unsafe = structuredClone(state);
  unsafe.snapshot.positions = [{ symbol: "000001", name: "<img onerror=alert(1)>",
    quantity: 100, available: 100, price: "10", cost: "9", market_value: "1000" }];
  nextResponse = { ok: true, connection: unsafe };
  await vm.runInContext("loadClientConnection()", context);
  assert.equal(query("#client-positions").children[0].children[1].textContent, "<img onerror=alert(1)>");
  query("#client-positions").children[0].children[7].children[0].listeners.click();
  assert.equal(query("#cost-price").value, "9");
  assert.equal(query("#quantity").value, "100");

  // Given 旧快照 When 超过有效期 Then 页面独立显示过期状态。
  unsafe.snapshot.captured_at = new Date(Date.now() - 61000).toISOString();
  await vm.runInContext("loadClientConnection()", context);
  assert.match(query("#client-freshness").textContent, /过期/);
  assert.equal(query("#client-positions").children[0].children[7].children[0].disabled, true);

  // Given 过期自选 When 尝试使用旧按钮 Then 不再带入研究表单。
  unsafe.watchlist.captured_at = new Date(Date.now() - 61000).toISOString();
  await vm.runInContext("loadClientConnection()", context);
  query("#symbol").value = "600000";
  query("#client-watchlist").children[0].children[2].children[0].listeners.click();
  assert.equal(query("#symbol").value, "600000");
  assert.match(query("#client-watchlist-freshness").textContent, /过期/);

  // Given 当前是账户自动同步 When 开启自选自动同步 Then 停止另一通道避免页面冲突。
  nextResponse = { ok: true, connection: structuredClone(state) };
  query("#client-auto").checked = true;
  query("#client-watchlist-auto").checked = true;
  await query("#client-watchlist-auto").listeners.change({ target: query("#client-watchlist-auto") });
  assert.equal(query("#client-auto").checked, false);
  console.log("通过：双通道同步、独立过期、股票分析联动、权限阻断、文本转义与自动读取互斥。");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
