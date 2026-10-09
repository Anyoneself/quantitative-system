const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

async function main() {
  const url = process.argv[2];
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(url || "")) throw new Error("需要本机测试地址");
  const output = "/tmp/quant-client-risk-ui";
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
  });
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 1000 } });
      page.setDefaultTimeout(7000);
      const errors = [], mutations = [];
      page.on("pageerror", (error) => errors.push(error.message));
      const snapshot = {
        snapshot_id: "test-snapshot", captured_at: new Date().toISOString(),
        funds: { total_assets: "20000", market_value: "10000", cash_balance: "10000",
          available_cash: "10000", withdrawable_cash: "9000" },
        positions: [{ symbol: "600001", name: "测试证券", quantity: 1000, available: 400,
          market_value: "10000", price: "10", cost: "9" }],
      };
      const risk = {
        scope: "current_holdings", snapshot_id: snapshot.snapshot_id, status: "LOCAL_ONLY",
        stock_exposure: .5, max_position_weight: .5, locked_weight: .3,
        annualized_volatility: null, var_95: null, beta: null, history_curve: [],
        risk_level: "HIGH", risk_level_text: "高集中度", sample_status: "仅本地结构风险",
        benchmark_symbol: "000300", sample_size: 0, warnings: [],
        explanation: "当前权重历史情景，非账户真实净值。",
      };
      const connection = { request_token: "test-only", client: { code: "READY_TO_READ" },
        capabilities: { read_account: true, watchlist_sync: true, launch: true },
        snapshot: null, risk: null, stale: true, watchlist: null, watchlist_stale: true,
        public_history_enabled: false };
      let finish = true;
      await page.route("**/api/client{,/**}", async (route) => {
        const request = route.request();
        const action = new URL(request.url()).pathname.split("/")[3];
        if (action) {
          mutations.push(action);
          assert.equal(request.headers()["x-desktop-token"], "test-only");
          assert.deepEqual(request.postDataJSON(), {});
          assert.ok(["sync", "history-enable", "history-disable"].includes(action));
          if (action === "sync") {
            connection.snapshot = snapshot;
            connection.risk = risk;
            connection.stale = false;
          } else {
            connection.public_history_enabled = action === "history-enable";
            risk.status = connection.public_history_enabled ? "ANALYZING" : "LOCAL_ONLY";
            if (!connection.public_history_enabled) Object.assign(risk, {
              annualized_volatility: null, var_95: null, beta: null, history_curve: [],
            });
          }
        } else if (connection.snapshot && connection.public_history_enabled && finish) {
          Object.assign(risk, {
            status: "READY", annualized_volatility: .24, var_95: .025, beta: .7,
            as_of_date: "2026-09-29", sample_size: 60, sample_status: "当前持仓历史情景 · 60 日",
            history_curve: [
              { trade_date: "2026-09-25", net_value: 1, benchmark_net_value: 1 },
              { trade_date: "2026-09-28", net_value: 1.01, benchmark_net_value: 1.005 },
              { trade_date: "2026-09-29", net_value: .98, benchmark_net_value: .99 },
            ],
          });
        }
        await route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, connection }) });
      });
      await page.route("**/api/dashboard**", (route) => route.fulfill({
        contentType: "application/json", body: JSON.stringify({ ok: true, dashboard: {
          summary: { total_assets: 50000 }, risk: {}, equity: [], allocation: [],
        } }),
      }));
      async function navigate(name) {
        if (width < 700) await page.locator("#menu-button").click();
        await page.locator(`.nav-item[data-page="${name}"]`).click();
      }
      // Given 本机已登录且未授权行情 When 打开总览 Then 自动分析本地结构但不代用户授权。
      await page.goto(url);
      await page.locator("#risk-sharpe").filter({ hasText: "50.00%" }).waitFor();
      assert.deepEqual(mutations, ["sync"]);
      assert.equal(await page.locator("#client-public-history").isChecked(), false);
      assert.equal(await page.locator("#risk-volatility").innerText(), "--");
      await page.waitForFunction(() => document.querySelector("#risk-radar-canvas").dataset.availableAxes === "3");
      assert.equal(await page.locator("#risk-radar-canvas").getAttribute("data-available-axes"), "3");

      // Given 用户允许向腾讯查询持仓代码 When 开启 Then 后台完成历史分析并刷新图表。
      await navigate("settings");
      await page.locator("#client-public-history").check();
      await navigate("overview");
      await page.locator("#risk-volatility").filter({ hasText: "24.00%" }).waitFor();
      assert.deepEqual(mutations, ["sync", "history-enable"]);
      assert.equal(await page.locator("#risk-sharpe").innerText(), "50.00%");
      assert.equal(await page.locator("#risk-win-rate").innerText(), "50.00%");
      assert.equal(await page.locator("#risk-profit-loss").innerText(), "30.00%");
      assert.equal(await page.locator("#risk-beta").innerText(), "0.70");
      assert.equal(await page.locator("#risk-var").innerText(), "2.50%");
      assert.match(await page.locator("#equity-title").innerText(), /历史情景/);
      assert.match(await page.locator("#equity-chart").getAttribute("aria-label"), /不是账户净值/);
      assert.match(await page.locator("#risk-panel .info-tip").getAttribute("title"), /非账户实际业绩/);
      assert.match(await page.locator("#risk-history-detail").innerText(), /非账户真实净值/);
      await page.waitForFunction(() => document.querySelector("#risk-radar-canvas").dataset.availableAxes === "5");
      const canvas = await page.evaluate(() => {
        const radar = document.querySelector("#risk-radar-canvas");
        const pixels = radar.getContext("2d").getImageData(0, 0, radar.width, radar.height).data;
        let colored = 0;
        for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 3] && pixels[i + 1] > pixels[i] + 40) colored++;
        return { colored, axes: radar.dataset.availableAxes, overflow: document.documentElement.scrollWidth > innerWidth };
      });
      assert.ok(canvas.colored > 50, "雷达必须绘制真实数据区域");
      assert.equal(canvas.axes, "5");
      assert.equal(canvas.overflow, false);
      await page.screenshot({ path: path.join(output, `risk-${width}.png`), fullPage: true, animations: "disabled" });
      if (width === 1440) await page.locator("#risk-panel").screenshot({
        path: path.join(output, "risk-panel-synthetic.png"), animations: "disabled",
      });

      // Given 用户关闭自动读取且历史行情不足 When 刷新 Then 保留结构指标，不填零或继续显示旧曲线。
      await navigate("settings");
      await page.locator("#client-auto").uncheck();
      await navigate("overview");
      finish = false;
      Object.assign(risk, { status: "PARTIAL", annualized_volatility: null, var_95: null, beta: null,
        sample_status: "历史样本不足", history_curve: [], warnings: ["部分持仓行情不可用"] });
      await page.locator("#dashboard-refresh").click();
      await page.waitForFunction(() => document.querySelector("#risk-volatility").textContent === "--");
      await page.waitForFunction(() => document.querySelector("#risk-radar-canvas").dataset.availableAxes === "3");
      assert.equal(await page.locator("#risk-radar-canvas").getAttribute("data-available-axes"), "3");
      assert.equal(await page.locator("#risk-var").innerText(), "--");
      assert.equal(await page.locator("#equity-empty").isVisible(), true);
      assert.match(await page.locator("#risk-history-detail").innerText(), /部分持仓行情不可用/);
      assert.equal(await page.locator("#client-auto").isChecked(), false);

      // Given 已启用历史估算 When 撤销授权 Then 恢复仅本地分析并清除旧曲线。
      await navigate("settings");
      await page.locator("#client-public-history").uncheck();
      await page.waitForFunction(() => !document.querySelector("#dashboard-public-history").checked);
      await navigate("overview");

      // Given 账户快照已过期 When 展示风险 Then 明确标记历史快照，不能继续表示当前风险。
      snapshot.captured_at = new Date(Date.now() - 61000).toISOString();
      connection.stale = true;
      await page.locator("#dashboard-refresh").click();
      await page.locator("#risk-history-detail").filter({ hasText: "历史快照" }).waitFor();
      assert.match(await page.locator("#total-assets").innerText(), /20,000/);
      assert.equal(await page.locator("#risk-radar-canvas").isVisible(), true);
      assert.equal(await page.locator("#risk-sharpe-label").innerText(), "股票仓位");
      assert.deepEqual(mutations, ["sync", "history-enable", "history-disable"]);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ width, automaticRisk: true, canvas, errors }));
      await page.close();
    }
  } finally { await browser.close(); }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
