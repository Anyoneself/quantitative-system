const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");

async function main() {
  const url = process.argv[2];
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(url || "")) throw new Error("需要本机测试地址");
  fs.mkdirSync("/tmp/quant-trade", { recursive: true });
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  try {
    for (const width of [1440, 719, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      page.setDefaultTimeout(8000);
      await page.addInitScript(() => {
        window.__tradeTimeOffset = 0;
        window.__copiedTradeFields = [];
        const now = Date.now.bind(Date);
        Date.now = () => now() + window.__tradeTimeOffset;
        Object.defineProperty(navigator, "clipboard", { value: {
          writeText: async text => { window.__copiedTradeFields.push(text); },
        } });
      });
      const errors = [], calls = [], tickets = [];
      page.on("pageerror", error => errors.push(error.message));
      let deferred = false, pending, failReview = false, failLaunch = false;
      const connection = {
        request_token: "test-only", client: { code: "READY_TO_READ", version: "5.3.5" },
        capabilities: { read_account: true, launch: true, watchlist_sync: true, live_trading: false },
        stale: false, watchlist_stale: false, public_history_enabled: false,
        snapshot: { snapshot_id: "account-test", captured_at: new Date().toISOString(),
          broker: "平安证券", account_id_verified: false,
          funds: { total_assets: "14000", market_value: "4000", cash_balance: "10000", available_cash: "9000", withdrawable_cash: "8000" },
          positions: [{ symbol: "600001", name: "测试持仓", quantity: 400, available: 250, price: "10", cost: "9", market_value: "4000" }] },
        watchlist: { captured_at: new Date().toISOString(), group_name: "测试自选", declared_count: 1,
          stocks: [{ symbol: "600002", name: "测试自选股" }] },
      };
      await page.route("**/api/**", async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        calls.push(path);
        let result = { ok: true, connection };
        if (path === "/api/client/prepare-trade") {
          assert.equal(request.headers()["x-desktop-token"], "test-only");
          const ticket = request.postDataJSON();
          tickets.push(ticket);
          if (failReview) return route.fulfill({ status: 422, contentType: "application/json",
            body: JSON.stringify({ ok: false, error: "请重新登录同花顺交易账户。", connection: { ...connection, stale: true } }) });
          result.draft = { ...ticket, status: "NOT_SUBMITTED", submitted: false, name: "测试股票",
            amount: (Number(ticket.price) * ticket.quantity).toFixed(2), snapshot_id: connection.snapshot.snapshot_id,
            checked_at: new Date().toISOString(), expires_at: new Date(Date.now() + 60000).toISOString(),
            available_cash: "9000.00", available_quantity: ticket.side === "SELL" ? 250 : 0,
            account_id_verified: false };
          if (deferred) { deferred = false; pending({ route, result }); return; }
        } else if (path === "/api/client/launch") {
          assert.deepEqual(request.postDataJSON(), {});
          if (failLaunch) return route.fulfill({ status: 422, contentType: "application/json",
            body: JSON.stringify({ ok: false, error: "客户端打开失败", connection }) });
        } else if (path === "/api/client/sync") {
          connection.stale = false;
          connection.snapshot.captured_at = new Date().toISOString();
          connection.snapshot.snapshot_id = "new-snapshot";
        } else if (path !== "/api/client") throw new Error(`意外接口 ${path}`);
        await route.fulfill({ contentType: "application/json", body: JSON.stringify(result) });
      });

      // Given 左侧交易栏目 When 直接访问 Then 展示买卖切换和真实账户快照，不误报已成交。
      await page.goto(`${url}/#trade`);
      await page.locator("#trade-page.active").waitFor();
      assert.equal(await page.locator('.nav-item[data-page="trade"]').count(), 1);
      await page.locator("#trade-cash").filter({ hasText: "9000.00" }).waitFor();
      assert.match(await page.locator("#trade-positions").innerText(), /测试持仓.*250/s);
      assert.match(await page.locator("#trade-connection-message").innerText(), /账户页面读取成功/);
      assert.equal(await page.locator("#trade-review").isEnabled(), true);
      assert.equal(await page.locator("#trade-draft").isVisible(), false);
      assert.equal(await page.locator("#trade-client-open").isEnabled(), false);
      assert.equal(calls.some(path => /buy|sell|prepare-trade/.test(path)), false);

      // Given 买入草稿 When 核对 Then 参数被正确发送且仅显示未提交，价格不从前复权行情自动带入。
      await page.locator("#trade-stock-picker").selectOption("600002");
      assert.equal(await page.locator("#trade-symbol").inputValue(), "600002");
      assert.equal(await page.locator("#trade-price").inputValue(), "");
      await page.locator("#trade-price").fill("10.25");
      await page.locator("#trade-quantity").fill("100");
      await page.locator("#trade-review").click();
      await page.locator("#trade-draft:not(.hidden)").waitFor();
      assert.deepEqual(tickets.at(-1), { side: "BUY", symbol: "600002", price: "10.25", quantity: 100 });
      assert.match(await page.locator("#trade-draft").innerText(), /未提交/);
      assert.match(await page.locator("#trade-draft").innerText(), /1025.00/);
      await page.getByRole("button", { name: "复制股票代码", exact: true }).click();
      await page.getByRole("button", { name: "复制限价", exact: true }).click();
      await page.getByRole("button", { name: "复制数量", exact: true }).click();
      assert.deepEqual(await page.evaluate(() => window.__copiedTradeFields), ["600002", "10.25", "100"]);
      assert.equal(await page.locator("#trade-client-open").isEnabled(), true);
      await page.locator("#trade-client-open").click();
      await page.locator("#trade-status").filter({ hasText: "客户端已打开" }).waitFor();
      assert.equal(calls.filter(path => path === "/api/client/launch").length, 1);
      assert.doesNotMatch(await page.locator("#trade-status").innerText(), /已委托|已成交|已下单/);
      await page.locator("#trade-price").fill("11");
      assert.equal(await page.locator("#trade-draft").isVisible(), false);
      assert.equal(await page.locator("#trade-client-open").isEnabled(), false);

      // Given 持仓有锁定数量 When 准备卖出 Then 仅带入证券及方向，不默认卖光或使用旧买价。
      await page.locator("#trade-positions").getByRole("button", { name: "准备卖出 测试持仓" }).click();
      assert.equal(await page.locator("#trade-sell").getAttribute("aria-selected"), "true");
      assert.equal(await page.locator("#trade-symbol").inputValue(), "600001");
      assert.equal(await page.locator("#trade-price").inputValue(), "");
      assert.equal(await page.locator("#trade-quantity").inputValue(), "");
      assert.match(await page.locator("#trade-availability").innerText(), /250/);
      await page.locator("#trade-price").fill("10");
      await page.locator("#trade-quantity").fill("250");
      await page.locator("#trade-review").click();
      await page.locator("#trade-draft:not(.hidden)").waitFor();
      assert.equal(tickets.at(-1).side, "SELL");
      assert.equal(tickets.at(-1).quantity, 250);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `/tmp/quant-trade/trade-${width}.png`, fullPage: true });

      // Given 草稿已核对 When 60秒过期 Then 禁止继续使用或复制旧委托。
      await page.evaluate(() => { window.__tradeTimeOffset = 61000; });
      await page.locator("#trade-status").filter({ hasText: "核对已失效" }).waitFor();
      assert.equal(await page.locator("#trade-draft").isVisible(), false);
      assert.equal(await page.locator("#trade-client-open").isEnabled(), false);
      await page.evaluate(() => { window.__tradeTimeOffset = 0; });

      // Given 已核对草稿 When 切页或账户快照变更 Then 必须重新核对。
      await page.locator("#trade-review").click();
      await page.locator("#trade-draft:not(.hidden)").waitFor();
      await page.evaluate(() => { location.hash = "settings"; });
      await page.locator("#settings-page.active").waitFor();
      await page.evaluate(() => { location.hash = "trade"; });
      await page.locator("#trade-page.active").waitFor();
      assert.equal(await page.locator("#trade-draft").isVisible(), false);
      await page.locator("#trade-review").click();
      await page.locator("#trade-draft:not(.hidden)").waitFor();
      connection.snapshot.snapshot_id = "changed-account";
      await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
      await page.locator("#trade-status").filter({ hasText: "核对已失效" }).waitFor();
      assert.equal(await page.locator("#trade-client-open").isEnabled(), false);

      // Given 核对尚未返回 When 用户改价 Then 迟到结果不能恢复旧草稿。
      deferred = true;
      const waiting = new Promise(resolve => { pending = resolve; });
      await page.locator("#trade-review").click();
      const old = await waiting;
      await page.locator("#trade-price").fill("12");
      await old.route.fulfill({ contentType: "application/json", body: JSON.stringify(old.result) });
      assert.equal(await page.locator("#trade-draft").isVisible(), false);
      assert.equal(await page.locator("#trade-client-open").isEnabled(), false);

      // Given 客户端断线 When 核对 Then 展示错误，不保留旧的可操作委托。
      failReview = true;
      await page.locator("#trade-review").click();
      await page.locator("#trade-error").filter({ hasText: "重新登录" }).waitFor();
      assert.equal(await page.locator("#trade-draft").isVisible(), false);
      assert.equal(await page.locator("#trade-client-open").isEnabled(), false);

      // Given 客户端打开失败 When 从交易页发起 Then 错误必须显示在当前栏目。
      failLaunch = true;
      await page.locator("#trade-launch").click();
      await page.locator("#trade-error").filter({ hasText: "客户端打开失败" }).waitFor();
      assert.equal(calls.some(path => /\/api\/client\/(buy|sell|cancel)$/.test(path)), false);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ width, draftOnly: true, clientHandoff: true, validation: true, errors }));
      await page.close();
    }
  } catch (error) {
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        const width = page.viewportSize().width;
        await page.screenshot({ path: `/tmp/quant-trade/failure-${width}.png`, fullPage: true });
        fs.writeFileSync(`/tmp/quant-trade/failure-${width}.html`, await page.content());
      }
    }
    throw error;
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
