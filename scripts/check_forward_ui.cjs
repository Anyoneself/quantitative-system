const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

async function main() {
  const url = process.argv[2];
  if (!url || !/^http:\/\/127\.0\.0\.1:\d+$/.test(url)) throw new Error("需要本机服务地址");
  const output = process.argv[3] || "/tmp/quant-forward-ui";
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
  });
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 1000 } });
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      let current = null;
      let rejectAdvance = false;
      let corrupt = false;
      let updating = false;
      const session = {
        session_id: "a".repeat(32), created_at: "2026-09-29T16:00:00+08:00", last_date: "2026-09-30",
        storage_revision: 2,
        start_after: "2026-09-29", status: "ACTIVE", qualified_days: 1, has_replay_gap: false,
        live_trading_enabled: false, config: { simulation: { initial_cash: 100000 } },
        result: { summary: { final_equity: 100010, total_return: .0001, max_drawdown: 0,
          trade_count: 1, fees: 5, halted: false }, equity_curve: [
          { date: "2026-09-30", equity: 100010 }], orders: [
          { trade_date: "2026-09-30", symbol: "<img onerror=alert(1)>", side: "BUY", filled_quantity: 100, fee: 5, reason: "FILLED" }],
        reconciliation: { status: "BALANCED", attribution: [
          { symbol: "600001", realized_pnl: 0, unrealized_pnl: 10, net_pnl: 10, fees: 5 }] } },
        next_intent: { signal_date: "2026-09-30", weights: { "600001": .2 }, rebalance: false },
        observations: [{ date: "2026-09-30", observed_at: "2026-09-30T16:00:00+08:00", mode: "FORWARD", intent_resolution: "RISK_REDUCTION" }],
        limitations: ["测试替身，不是真实收益。"], last_error: null,
        operations: { revision: 2, worker: { status: "HEARTBEAT_EXPIRED", online: false, heartbeat_at: "2026-09-30T15:00:00+08:00" },
          alerts: [{ code: "WORKER_OFFLINE", message: "后台观察进程离线" }],
          revisions: [{ revision: 2, event: "ADVANCE", saved_at: "2026-09-30T16:00:00+08:00", sha256: "f".repeat(64) }] },
        data_audit: { status: "REVIEW_REQUIRED", calendar_status: "UNVERIFIED", calendar_source: null,
          checks: [{ name: "公司行动", passed: false, detail: "未独立核验" }],
          missing_benchmark_dates: [], unexpected_benchmark_dates: [], missing_member_bars: [], price_jumps: [] },
        monitoring: {
          benchmark: { symbol: "000300", total_return: .02 },
          relative: { excess_return: -.0199, recent_20_day_excess: null, tracking_error: null, information_ratio: null },
          comparison_curve: [{ date: "2026-09-29", strategy: 1, benchmark: 1 },
            { date: "2026-09-30", strategy: 1.0001, benchmark: 1.02 }],
          portfolio: { cash: 98000, gross_exposure: .02, positions: [
            { symbol: "600001", quantity: 100, weight: .02, target_weight: .2, price: 20.1, mark_date: "2026-09-30", stale: false }] },
          execution: { orders: 1, filled_orders: 1, rejected_orders: 0, partial_orders: 0, fill_rate: 1,
            turnover_on_initial_cash: .02, modeled_opening_shortfall: 1, reasons: [] },
          evidence: { status: "INSUFFICIENT", checks: [
            { id: "active_ci", name: "日均主动收益区间下界", passed: null, value: null, threshold: 0 }] },
          alerts: [{ code: "RISK_SAMPLE", message: "体检测试替身" }],
          limitations: ["测试口径，不是真实业绩。"],
        },
      };
      await page.route("**/api/paper**", async (route) => {
        const request = route.request();
        let data;
        if (request.method() === "POST") {
          assert.equal(request.headers()["x-desktop-token"], "test-only");
          const action = new URL(request.url()).pathname.split("/").at(-1);
          if (["pause", "resume", "advance"].includes(action)) {
            assert.equal(typeof request.postDataJSON().expected_revision, "number");
            if (request.postDataJSON().expected_revision !== current.storage_revision) {
              await route.fulfill({ status: 409, contentType: "application/json",
                body: JSON.stringify({ ok: false, error: "账本版本已更新" }) });
              return;
            }
          }
          if (request.url().endsWith("/advance") && rejectAdvance) {
            await route.fulfill({ status: 502, contentType: "application/json", body: JSON.stringify({ ok: false, error: "行情不可用" }) });
            return;
          }
          const revision = current ? current.storage_revision + 1 : 2;
          current = structuredClone(session);
          current.storage_revision = revision;
          current.operations.revision = revision;
          if (request.url().endsWith("/pause")) current.status = "PAUSED";
          if (request.url().endsWith("/resume")) current.has_replay_gap = true;
          if (request.url().endsWith("/recover")) {
            corrupt = false;
            current.status = "PAUSED";
            current.has_replay_gap = true;
          }
          data = { ok: true, session: current };
        } else if (request.url().endsWith("/api/paper")) {
          data = { ok: true, request_token: "test-only", sessions: current
            ? [updating ? { session_id: current.session_id, busy: true }
              : corrupt ? { session_id: current.session_id, error: "账本校验失败" } : current] : [] };
        } else {
          if (corrupt) {
            await route.fulfill({ status: 422, contentType: "application/json",
              body: JSON.stringify({ ok: false, error: "账本校验失败，需要恢复" }) });
            return;
          }
          data = { ok: true, session: current };
        }
        await route.fulfill({ contentType: "application/json", body: JSON.stringify(data) });
      });
      await page.goto(`${url}/#paper`);
      // Given 空账本 When 创建 Then 使用本机令牌、展示冻结状态和净值。
      await page.locator("#paper-create").waitFor({ state: "visible" });
      await page.locator("#paper-symbols").fill("600001");
      await page.locator("#paper-create").click();
      await page.locator("#paper-result").waitFor({ state: "visible" });
      assert.match(await page.locator("#paper-equity").innerText(), /100,010/);
      assert.equal(await page.locator("#paper-days").innerText(), "1 / 126");
      assert.equal(await page.locator("#paper-orders img").count(), 0);
      assert.match(await page.locator("#paper-orders").innerText(), /<img/);
      assert.match(await page.locator("#paper-worker").innerText(), /离线.*心跳超时/);
      assert.match(await page.locator("#paper-reconciliation").innerText(), /平衡/);
      assert.match(await page.locator("#paper-attribution").innerText(), /600001/);
      assert.match(await page.locator("#paper-data-checks").innerText(), /未独立核验/);
      assert.match(await page.locator("#paper-revisions").innerText(), /推进/);
      assert.match(await page.locator("#paper-benchmark-return").innerText(), /2.00%/);
      assert.match(await page.locator("#paper-excess").innerText(), /-1.99%/);
      assert.match(await page.locator("#paper-positions").innerText(), /600001/);
      assert.match(await page.locator("#paper-fill-rate").innerText(), /100.00%/);
      assert.match(await page.locator("#paper-checks").innerText(), /样本不足/);
      assert.match(await page.locator("#paper-observations").innerText(), /风控优先减仓/);
      const downloadEvent = page.waitForEvent("download");
      await page.locator("#paper-download").click();
      const download = await downloadEvent;
      assert.match(download.suggestedFilename(), /paper-.*-r2\.json$/);
      await page.waitForTimeout(150);
      const geometry = await page.evaluate(() => {
        const canvas = document.querySelector("#paper-chart");
        const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
        let colored = 0, benchmarkPixels = 0;
        for (let i = 0; i < pixels.length; i += 4) {
          if (pixels[i + 3] && pixels[i + 2] > pixels[i] + 40) colored++;
          if (pixels[i + 3] && pixels[i + 1] > pixels[i] + 70 && Math.abs(pixels[i + 1] - pixels[i + 2]) < 20) benchmarkPixels++;
        }
        return { overflow: document.documentElement.scrollWidth > innerWidth, colored, benchmarkPixels };
      });
      assert.equal(geometry.overflow, false);
      assert.ok(geometry.colored > 100);
      assert.ok(geometry.benchmarkPixels > 100, "真实基准必须绘制独立曲线");
      await page.screenshot({ path: path.join(output, `paper-${width}.png`), fullPage: true });
      // Given 网络失败 When 推进 Then 原净值保留且关闭自动检查。
      rejectAdvance = true;
      await page.locator("#paper-auto").check();
      await page.locator("#paper-advance").click();
      await page.locator("#paper-error").waitFor({ state: "visible" });
      assert.equal(await page.locator("#paper-auto").isChecked(), false);
      assert.match(await page.locator("#paper-equity").innerText(), /100,010/);
      assert.equal(await page.locator("#paper-pause").isDisabled(), true);
      await page.locator("#paper-refresh").click();
      await page.locator("#paper-status").filter({ hasText: "已登记" }).waitFor();
      // Given 页面版本落后 When 暂停 Then 服务拒绝过期版本且操作需要重新核验。
      current.storage_revision += 1;
      page.on("dialog", (dialog) => dialog.accept());
      await page.locator("#paper-pause").click();
      await page.locator("#paper-error").filter({ hasText: "账本版本已更新" }).waitFor();
      assert.equal(current.status, "ACTIVE");
      assert.equal(await page.locator("#paper-download").isDisabled(), true);
      await page.locator("#paper-refresh").click();
      await page.locator("#paper-status").filter({ hasText: "已登记" }).waitFor();
      // Given 用户停止 When 确认 Then 禁止后续推进。
      await page.locator("#paper-pause").click();
      await page.locator("#paper-status").filter({ hasText: "已暂停" }).waitFor();
      assert.equal(await page.locator("#paper-advance").isDisabled(), true);
      // Given 已暂停 When 继续观察 Then 显示证据缺口。
      await page.locator("#paper-resume").click();
      await page.locator("#paper-status").filter({ hasText: "已登记" }).waitFor();
      assert.match(await page.locator("#paper-evidence").innerText(), /证据受限/);
      // Given 写进程持锁 When 刷新 Then 标记更新中而不展示损坏恢复入口。
      updating = true;
      await page.locator("#paper-refresh").click();
      await page.locator("#paper-error").filter({ hasText: "正在更新" }).waitFor();
      assert.equal(await page.locator("#paper-recover").isVisible(), false);
      assert.equal(await page.locator("#paper-result").isVisible(), false);
      updating = false;
      // Given 损坏记录 When 刷新及恢复 Then 隐藏旧会话、保留操作令牌并恢复为暂停。
      corrupt = true;
      await page.locator("#paper-refresh").click();
      await page.locator("#paper-error").filter({ hasText: "需要恢复" }).waitFor();
      assert.equal(await page.locator("#paper-result").isVisible(), false);
      await page.locator("#paper-recover").click();
      await page.locator("#paper-status").filter({ hasText: "已暂停" }).waitFor();
      assert.equal(await page.locator("#paper-recover").isVisible(), false);
      assert.equal(await page.locator("#paper-auto").isChecked(), false);
      await page.screenshot({ path: path.join(output, `paper-recovered-${width}.png`), fullPage: true });
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ width, geometry, errors, lifecycle: "passed" }));
      await page.close();
    }
  } finally { await browser.close(); }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
