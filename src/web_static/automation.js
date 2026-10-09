const automationForm = document.querySelector("#automation-form");
const automationFields = {
  capital_limit: "#automation-capital", max_order_amount: "#automation-order-limit",
  max_position_percent: "#automation-position-limit", daily_loss_limit: "#automation-loss-limit",
  buy_rule: "#automation-buy-rule", sell_rule: "#automation-sell-rule",
};
let automationRevision = null;
let automationDirty = false;
let automationSaving = false;
let automationEditVersion = 0;

function setAutomationError(message) {
  setText("#automation-error", message);
  document.querySelector("#automation-error").classList.toggle("hidden", !message);
}

function restoreAutomationForm() {
  const state = clientConnection?.automation;
  for (const [key, selector] of Object.entries(automationFields)) {
    document.querySelector(selector).value = state?.config?.[key] ?? "";
  }
  document.querySelector("#automation-symbols").value = state?.config?.symbols.join(", ") || "";
  document.querySelector("#automation-existing").value = state?.config
    ? String(state.config.allow_existing_positions) : "";
  automationRevision = state?.revision ?? null;
}

automationForm.addEventListener("input", () => {
  automationDirty = true;
  automationEditVersion++;
  setAutomationError("");
  renderAutomation();
});

document.querySelector("#automation-reset").addEventListener("click", () => {
  automationDirty = false;
  automationEditVersion++;
  setAutomationError("");
  renderAutomation();
});
document.querySelector("#automation-refresh").addEventListener("click", () => loadClientConnection());

automationForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (clientBusy || automationSaving || automationRevision === null) return;
  const config = {};
  for (const [key, selector] of Object.entries(automationFields)) {
    config[key] = document.querySelector(selector).value.trim();
  }
  config.symbols = document.querySelector("#automation-symbols").value.trim().split(/[,，\s]+/).filter(Boolean);
  const existing = document.querySelector("#automation-existing").value;
  if (!["true", "false"].includes(existing)) return setAutomationError("请明确是否允许自动卖出现有持仓。");
  config.allow_existing_positions = existing === "true";
  const version = automationEditVersion;
  const expectedRevision = automationRevision;
  automationSaving = clientBusy = true;
  setAutomationError("");
  renderClientConnection();
  try {
    const response = await clientRequest("automation-save", { config, expected_revision: expectedRevision });
    const state = response.connection?.automation;
    if (!state || state.revision !== expectedRevision + 1 || state.running !== false || state.can_start !== false) {
      throw new Error("保存结果无法核验，请检查状态后再操作；未请求启动实盘。");
    }
    automationRevision = state.revision;
    if (version === automationEditVersion) automationDirty = false;
    if (currentPage === "automation") showToast("草案已保存，未启动实盘");
  } catch (error) {
    automationDirty = true;
    invalidateClientView(error);
    setAutomationError(error.name === "AbortError"
      ? "保存超时，结果待核验；请检查状态，未请求启动实盘。" : error.message);
  } finally {
    automationSaving = clientBusy = false;
    renderClientConnection();
  }
});

function renderAutomation() {
  const state = clientConnection?.automation;
  if (!automationDirty && !automationSaving) restoreAutomationForm();
  document.querySelector("#automation-save").disabled = clientBusy || !state || Boolean(state.storage_error)
    || automationRevision === null || !clientConnection?.request_token;
  document.querySelector("#automation-refresh").disabled = clientBusy;
  document.querySelector("#automation-reset").disabled = clientBusy || !automationDirty || !state || Boolean(state.storage_error);
  // 当前版本没有执行器，任何接口字段都不能启用实盘按钮。
  document.querySelector("#automation-start").disabled = true;
  setText("#automation-save", automationSaving ? "正在保存..." : "保存草案");
  setText("#automation-saved", !state ? "尚未读取配置" : state.storage_error ? "配置无法校验"
    : state.config ? `已存草案 v${state.revision} · ${new Date(state.saved_at).toLocaleString("zh-CN")}` : "尚未保存草案");
  const conflict = automationDirty && state && automationRevision !== state.revision;
  setText("#automation-edit-state", conflict ? "存档版本已变化，请先核对差异" : automationDirty ? "本页有未保存修改" : "");
  setText("#automation-checked", state?.checked_at ? `检查时间：${new Date(state.checked_at).toLocaleString("zh-CN")}` : "");
  const checks = document.querySelector("#automation-checks");
  checks.replaceChildren();
  for (const item of state?.checks || [{ label: "接入状态", passed: false, detail: "尚未获取实盘接入检查结果。" }]) {
    const expired = item.id === "snapshot" && clientSnapshotStale("positions");
    const row = document.createElement("li");
    const title = document.createElement("strong");
    const badge = document.createElement("span");
    const detail = document.createElement("p");
    title.textContent = item.label;
    badge.textContent = item.passed && !expired ? "已核对" : "未满足";
    badge.className = item.passed && !expired ? "automation-pass" : "automation-blocked";
    detail.textContent = expired ? "账户快照缺失或已过期，请在买入卖出页重新同步。" : item.detail;
    row.append(title, badge, detail);
    checks.append(row);
  }
  if (state?.storage_error) setAutomationError(state.storage_error);
}

renderAutomation();
