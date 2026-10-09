const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

async function main() {
  const url = process.argv[2];
  if (!url || !/^http:\/\/127\.0\.0\.1:\d+$/.test(url)) throw new Error("需要本机测试服务地址");
  const output = process.argv[3] || "/tmp/quant-client-ui";
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
  });
  try {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      const requests = [];
      const mutations = [];
      page.setDefaultTimeout(5000);
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("request", (request) => {
        if (request.method() !== "GET") mutations.push(new URL(request.url()).pathname);
      });
      const captured = new Date().toISOString();
      const connection = {
        client: { code: "READY_TO_READ", version: "5.3.5", message: "模拟客户端，用于界面验收" },
        request_token: "test-only",
        capabilities: { launch: true, read_account: true, watchlist_sync: true, live_trading: false },
        stale: false, watchlist_stale: false,
        snapshot: {
          captured_at: captured,
          funds: { total_assets: "10000", cash_balance: "10000", market_value: "2000",
            available_cash: "8000", withdrawable_cash: "8000" },
          funds_reconciliation: { scope: "client_display", cash_basis: "available_cash" },
          positions: [{ symbol: "000001", name: "测试持仓", quantity: 200, available: 100,
            cost: "9.0000", price: "10.0000", market_value: "2000" }],
        },
        watchlist: {
          captured_at: captured, group_name: "自选股测试分组", declared_count: 2,
          stocks: [{ symbol: "000001", name: "测试自选" }, { symbol: "688001", name: "测试科创" }],
        },
      };
      let accountFailure = false;
      let offline = false;
      await page.route("**/api/client{,/**}", async (route) => {
        if (offline) return route.abort();
        const request = route.request();
        const action = new URL(request.url()).pathname.split("/")[3];
        requests.push(action || "status");
        if (action) {
          assert.equal(request.headers()["x-desktop-token"], "test-only");
          assert.deepEqual(request.postDataJSON(), {});
        }
        const failed = accountFailure && action === "sync";
        if (failed) {
          connection.stale = true;
          connection.last_error = { code: "TRADE_PAGE_REQUIRED", message: "交易账户未登录" };
        }
        await route.fulfill({ status: failed ? 422 : 200, contentType: "application/json",
          body: JSON.stringify({ ok: !failed, error: failed ? "交易账户未登录" : undefined, connection }) });
      });
      const ledger = { portfolio: { id: 1, name: "独立测试账本" },
        summary: { total_assets: 50000, daily_profit: 25, total_return: .1, max_drawdown: .02 },
        equity: [{ trade_date: "2026-09-28", net_value: 1 }, { trade_date: "2026-09-29", net_value: 1.1 }],
        risk: { annualized_volatility: .2, risk_level_text: "测试账本风险" }, allocation: [] };
      await page.route("**/api/dashboard**", (route) => route.fulfill({
        contentType: "application/json", body: JSON.stringify({ ok: true, dashboard: ledger }),
      }));
      await page.goto(`${url}/#client`);
      await page.locator("#settings-page.active").waitFor();
      await page.locator("#client-auto").uncheck();
      await page.locator("#client-positions .client-stock-action").waitFor();
      assert.match(await page.locator("#client-funds-reconciliation").innerText(), /原值保留.*原因未核实/);
      assert.equal(await page.locator("#client-cash").innerText(), "10,000.00");
      assert.equal(await page.locator("#client-available").innerText(), "8,000.00");
      assert.equal(await page.locator("#client-watchlist tr").count(), 2);
      await page.locator("#client-sync-watchlist").click();
      await page.waitForFunction(() => !document.querySelector("#client-sync-watchlist").disabled);
      assert.ok(requests.includes("sync-watchlist"));
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      const actionBox = await page.locator("#client-watchlist .client-stock-action").first().boundingBox();
      assert.ok(actionBox.x >= 0 && actionBox.x + actionBox.width <= viewport.width);
      await page.screenshot({ path: path.join(output, `client-${viewport.width}.png`), fullPage: true });

      // Given 客户端已同步且另有组合账本 When 进入总览 Then 只展示客户端且不混用历史业绩。
      if (viewport.width < 720) await page.locator("#menu-button").click();
      await page.locator('.nav-item[data-page="overview"]').click();
      await page.waitForFunction(() => document.querySelector("#total-assets").textContent.includes("10,000"));
      assert.match(await page.locator("#dashboard-source-status").innerText(), /同花顺.*平安证券/);
      assert.equal(await page.locator("#daily-profit-label").innerText(), "持仓市值");
      assert.match(await page.locator("#daily-profit").innerText(), /2,000/);
      assert.match(await page.locator("#total-return").innerText(), /8,000/);
      assert.equal(await page.locator("#max-drawdown").innerText(), "1");
      await page.locator(".holding-raw-details > summary").click();
      assert.equal(await page.locator("#dashboard-positions tr").count(), 1);
      assert.match(await page.locator("#dashboard-positions").innerText(), /000001.*测试持仓/);
      assert.equal(await page.locator("#dashboard-watchlist tr").count(), 2);
      assert.match(await page.locator("#dashboard-cash-balance").innerText(), /10,000/);
      assert.match(await page.locator(".empty-donut strong").innerText(), /10,000/);
      assert.equal(await page.locator("#risk-volatility").innerText(), "--");
      assert.match(await page.locator("#equity-empty-message").innerText(), /历史净值.*未接入/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: path.join(output, `overview-client-${viewport.width}.png`), fullPage: true, animations: "disabled" });

      // Given 已移除组合账本入口 When 查看总览 Then 不能切换至另一种账户口径。
      assert.equal(await page.locator("#dashboard-source").isVisible(), false);
      assert.deepEqual(await page.locator("#dashboard-source option").evaluateAll(nodes => nodes.map(n => n.value)), ["client"]);
      assert.equal(await page.locator("#risk-volatility").innerText(), "--");
      assert.equal(await page.locator("#equity-empty").isVisible(), true);

      // Given 账户快照过期 When 刷新或网络断开 Then 保留历史值并提示过期，不伪装为当前状态。
      connection.snapshot.captured_at = new Date(Date.now() - 61000).toISOString();
      await page.locator("#dashboard-refresh").click();
      await page.locator("#dashboard-source-status").filter({ hasText: "已过期" }).waitFor();
      assert.match(await page.locator("#total-assets").innerText(), /10,000/);
      assert.doesNotMatch(await page.locator("#dashboard-watchlist-status").innerText(), /已过期/);
      offline = true;
      await page.locator("#dashboard-refresh").click();
      await page.waitForFunction(() => !document.querySelector("#dashboard-refresh").disabled);
      assert.match(await page.locator("#dashboard-source-status").innerText(), /已过期/);
      assert.match(await page.locator("#total-assets").innerText(), /10,000/);
      offline = false;
      connection.snapshot.captured_at = new Date().toISOString();
      connection.stale = false;
      connection.watchlist_stale = false;

      // Given 只有自选股 When 刷新 Then 展示自选且资金保持未知，不把自选伪装成持仓。
      const savedSnapshot = connection.snapshot;
      connection.snapshot = null;
      await page.locator("#dashboard-refresh").click();
      await page.waitForFunction(() => document.querySelector("#total-assets").textContent === "--");
      assert.equal(await page.locator("#max-drawdown").innerText(), "--");
      assert.match(await page.locator("#dashboard-positions").innerText(), /尚未同步/);
      assert.equal(await page.locator("#dashboard-watchlist tr").count(), 2);

      // Given 已清空客户端快照 When 刷新 Then 显示未同步，不回退到另一种账本。
      const savedWatchlist = connection.watchlist;
      connection.watchlist = null;
      await page.locator("#dashboard-refresh").click();
      await page.waitForFunction(() => document.querySelector("#total-assets").textContent === "--");
      assert.match(await page.locator("#dashboard-source-status").innerText(), /尚未同步/);
      connection.snapshot = savedSnapshot;
      connection.watchlist = savedWatchlist;
      await page.locator("#dashboard-refresh").click();
      await page.waitForFunction(() => document.querySelector("#total-assets").textContent.includes("10,000"));
      assert.deepEqual(mutations, ["/api/client/sync-watchlist"], "总览读取不得触发同步、交易或账本写入");

      // Given 服务已有快照 When 直接打开或重载总览 Then 无需先进入客户端页。
      await page.goto(url);
      await page.waitForFunction(() => document.querySelector("#total-assets").textContent.includes("10,000"));
      assert.equal(await page.locator("#dashboard-client-details").isVisible(), true);
      await page.reload();
      await page.waitForFunction(() => document.querySelector("#total-assets").textContent.includes("10,000"));
      await page.locator(".holding-raw-details > summary").click();
      if (viewport.width < 720) await page.locator("#menu-button").click();
      await page.locator('.nav-item[data-page="settings"]').click();
      await page.locator("#client-auto").uncheck();
      if (viewport.width < 720) await page.locator("#menu-button").click();
      await page.locator('.nav-item[data-page="overview"]').click();

      // Given 页面停留超过有效期 When 没有刷新请求 Then 仍自动标记旧快照过期。
      connection.snapshot.captured_at = new Date(Date.now() - 58000).toISOString();
      await page.locator("#dashboard-refresh").click();
      await page.waitForFunction(() => !document.querySelector("#dashboard-refresh").disabled);
      const requestCount = requests.length;
      await page.locator("#dashboard-source-status").filter({ hasText: "已过期" }).waitFor();
      assert.equal(requests.length, requestCount);

      // Given 已验证空仓与大额资金 When 渲染 Then 区分零与未知，并保持金额在卡片内。
      connection.snapshot = { ...savedSnapshot, captured_at: new Date().toISOString(), positions: [],
        funds: { total_assets: "1000000000000", market_value: "0", cash_balance: "1000000000000",
          available_cash: "1000000000000", withdrawable_cash: "1000000000000" } };
      await page.locator("#dashboard-refresh").click();
      await page.waitForFunction(() => document.querySelector("#max-drawdown").textContent === "0");
      assert.match(await page.locator("#dashboard-positions").innerText(), /持仓为零/);
      assert.equal(await page.evaluate(() => [...document.querySelectorAll("#overview-page .metric-card strong")]
        .every((node) => node.scrollWidth <= node.clientWidth + 1)), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      connection.snapshot = savedSnapshot;
      connection.snapshot.captured_at = new Date().toISOString();
      connection.snapshot.positions[0].name = "<img onerror=alert(1)>";
      await page.locator("#dashboard-refresh").click();
      await page.locator("#dashboard-positions").filter({ hasText: "<img" }).waitFor();
      assert.equal(await page.locator("#dashboard-positions img").count(), 0);
      connection.snapshot.positions[0].name = "测试持仓";

      if (viewport.width < 720) await page.locator("#menu-button").click();
      await page.locator('.nav-item[data-page="settings"]').click();
      await page.waitForFunction(() => !document.querySelector("#client-sync").disabled);

      // Given 已同步持仓 When 带入分析 Then 保留真实输入口径且不自动发起行情或交易。
      await page.locator("#client-positions .client-stock-action").click();
      await page.locator("#market-page.active").waitFor();
      assert.equal(await page.locator("#cost-price").inputValue(), "9.0000");
      assert.equal(await page.locator("#quantity").inputValue(), "200");
      await page.locator("#client-stock-picker").selectOption("watchlist:688001");
      assert.equal(await page.locator("#symbol").inputValue(), "688001");
      assert.equal(await page.locator("#cost-price").inputValue(), "");
      assert.equal(await page.locator("#quantity").inputValue(), "");
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: path.join(output, `client-market-${viewport.width}.png`), fullPage: true });

      if (viewport.width < 720) await page.locator("#menu-button").click();
      await page.locator('.nav-item[data-page="settings"]').click();
      await page.waitForFunction(() => !document.querySelector("#client-sync").disabled);
      accountFailure = true;
      await page.locator("#client-sync").click();
      await page.locator("#client-account-error").filter({ hasText: "交易账户未登录" }).waitFor();
      assert.equal(await page.locator("#client-positions .client-stock-action").isEnabled(), false);
      assert.equal(await page.locator("#client-watchlist .client-stock-action").first().isEnabled(), true);

      // Given 自选已过期 When 刷新状态 Then 保留列表但禁用研究操作。
      connection.watchlist.captured_at = new Date(Date.now() - 61000).toISOString();
      await page.locator("#client-check").click();
      await page.locator("#client-watchlist-freshness").filter({ hasText: "已过期" }).waitFor();
      assert.equal(await page.locator("#client-watchlist .client-stock-action").first().isEnabled(), false);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ viewport, watchlist: true, holdingPrefill: true, independentErrors: true,
        overview: "client-only snapshots, expiry, offline, clear, reload", errors }));
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
