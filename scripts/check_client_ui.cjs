const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// 使用最小 DOM 替身测试状态变化，不连接真实账户或启动浏览器。
function element() {
  const classes = new Set();
  return {
    textContent: "", disabled: false, checked: false, children: [], listeners: {},
    classList: {
      toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
    addEventListener(name, listener) { this.listeners[name] = listener; },
    replaceChildren() { this.children = []; },
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
  const context = vm.createContext({
    console, AbortController, Date, setTimeout, clearTimeout, currentPage: "client",
    document: { hidden: false, querySelector: query, addEventListener() {} },
    window: { location: { hash: "" }, setTimeout, clearTimeout, setInterval(fn) { interval = fn; } },
    setText: (selector, value) => { query(selector).textContent = value; },
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
    capabilities: { launch: true, read_account: true, live_trading: false },
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

  // Given 旧快照 When 超过有效期 Then 页面独立显示过期状态。
  unsafe.snapshot.captured_at = new Date(Date.now() - 61000).toISOString();
  await vm.runInContext("loadClientConnection()", context);
  assert.match(query("#client-freshness").textContent, /过期/);
  console.log("通过：账户渲染、操作校验、失败保留、权限阻断、自动同步暂停、文本转义、快照过期。");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
