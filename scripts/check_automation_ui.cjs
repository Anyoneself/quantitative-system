const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// 隔离 DOM 与请求，不读取真实账户、不写入用户配置、不启动浏览器。
function element() {
  const classes = new Set();
  return { value: "", textContent: "", disabled: false, children: [], listeners: {},
    classList: { toggle(key, on) { if (on) classes.add(key); else classes.delete(key); }, contains(key) { return classes.has(key); } },
    addEventListener(key, callback) { this.listeners[key] = callback; },
    replaceChildren() { this.children = []; }, append(...values) { this.children.push(...values); } };
}

async function main() {
  const nodes = new Map(), calls = [], toasts = [];
  const query = key => { if (!nodes.has(key)) nodes.set(key, element()); return nodes.get(key); };
  const initial = { mode: "LIVE", status: "BLOCKED", running: false, can_start: false, config: null,
    revision: 0, saved_at: null, checked_at: new Date().toISOString(), storage_error: null,
    checks: [{ id: "snapshot", label: "账户读取", passed: true, detail: "快照已读取" },
      { id: "execution", label: "执行器", passed: false, detail: "未实现" }] };
  let requestImpl, stale = false;
  const context = vm.createContext({ Date, console, currentPage: "automation", clientConnection: null, clientBusy: false,
    document: { querySelector: query, createElement: element },
    setText: (key, value) => { query(key).textContent = value; },
    showToast: message => toasts.push(message),
    clientSnapshotStale: () => stale,
    renderClientConnection: () => vm.runInContext("renderAutomation()", context),
    invalidateClientView: error => vm.runInContext("setAutomationError(message)", Object.assign(context, {message: error.message})),
    loadClientConnection: async () => {},
    clientRequest: async (action, body) => {
      calls.push({action, body: JSON.parse(JSON.stringify(body))});
      return requestImpl(action, body);
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../src/web_static/automation.js"), "utf8"), context);
  const render = () => vm.runInContext("renderAutomation()", context);
  const submit = () => query("#automation-form").listeners.submit({preventDefault() {}});
  const edit = (key, value) => { query(key).value = value; query("#automation-form").listeners.input(); };

  // Given 首次打开 When 状态尚未返回 Then 不填默认资金，不允许保存或启动。
  assert.equal(query("#automation-save").disabled, true);
  assert.equal(query("#automation-start").disabled, true);
  assert.equal(query("#automation-capital").value, "");
  assert.equal(calls.length, 0);
  context.clientConnection = { request_token: "test-only", automation: structuredClone(initial) };
  render();
  assert.equal(query("#automation-save").disabled, false);
  const inputs = { "#automation-capital": "10000", "#automation-order-limit": "1000",
    "#automation-position-limit": "20", "#automation-loss-limit": "200",
    "#automation-symbols": "600001，000001", "#automation-buy-rule": "测试买入规则",
    "#automation-sell-rule": "测试卖出规则", "#automation-existing": "false" };
  for (const [key, value] of Object.entries(inputs)) edit(key, value);

  // Given 已填写草案 When 保存 Then 仅调用保存接口，不产生启动或下单请求。
  requestImpl = async (action, body) => {
    assert.equal(action, "automation-save");
    context.clientConnection.automation = { ...structuredClone(initial), config: JSON.parse(JSON.stringify(body.config)),
      revision: body.expected_revision + 1, saved_at: new Date().toISOString() };
    return {connection: context.clientConnection};
  };
  await submit();
  assert.equal(calls.at(-1).body.expected_revision, 0);
  assert.deepEqual(calls.at(-1).body.config.symbols, ["600001", "000001"]);
  assert.equal(calls.at(-1).body.config.allow_existing_positions, false);
  assert.match(toasts.at(-1), /未启动实盘/);
  assert.equal(query("#automation-start").disabled, true);

  // Given 保存未返回 When 用户继续编辑 Then 迟到响应不能覆盖新内容。
  let complete;
  requestImpl = (action, body) => new Promise(resolve => { complete = () => {
    context.clientConnection.automation = { ...context.clientConnection.automation,
      config: JSON.parse(JSON.stringify(body.config)), revision: body.expected_revision + 1 };
    resolve({connection: context.clientConnection});
  }; });
  edit("#automation-capital", "12000");
  const pending = submit();
  edit("#automation-capital", "15000");
  complete();
  await pending;
  assert.equal(query("#automation-capital").value, "15000");
  assert.match(query("#automation-edit-state").textContent, /未保存/);

  // Given 其他页面更新版本 When 刷新状态 Then 保留本页修改和旧预期版本，不能静默覆盖。
  context.clientConnection.automation.revision = 3;
  render();
  assert.equal(query("#automation-capital").value, "15000");
  assert.match(query("#automation-edit-state").textContent, /版本已变化/);
  requestImpl = async () => { throw new Error("版本冲突"); };
  await submit();
  assert.equal(calls.at(-1).body.expected_revision, 2);
  assert.equal(query("#automation-capital").value, "15000");
  assert.equal(query("#automation-error").textContent, "版本冲突");
  query("#automation-reset").listeners.click();
  assert.equal(query("#automation-capital").value, "12000");

  // Given 返回或页面状态被伪造 When can_start变真或快照过期 Then 实盘仍禁用且读取检查失效。
  context.clientConnection.automation.can_start = true;
  stale = true;
  render();
  assert.equal(query("#automation-start").disabled, true);
  assert.equal(query("#automation-checks").children[0].children[1].textContent, "未满足");

  // Given 网络超时或存档损坏 When 保存或检查 Then 不清掉输入、不声称启动、不继续保存。
  edit("#automation-buy-rule", "<img onerror=alert(1)>");
  requestImpl = async () => { const error = new Error("timeout"); error.name = "AbortError"; throw error; };
  await submit();
  assert.match(query("#automation-error").textContent, /结果待核验/);
  assert.equal(query("#automation-buy-rule").value, "<img onerror=alert(1)>");
  context.clientConnection.automation.storage_error = "存档无法校验";
  render();
  assert.equal(query("#automation-save").disabled, true);
  assert.equal(query("#automation-start").disabled, true);
  assert.equal(calls.every(call => call.action === "automation-save"), true);
  console.log("通过：实盘草案保存、迟到响应、版本冲突、异常保留、过期检查和禁止自动启动。");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
