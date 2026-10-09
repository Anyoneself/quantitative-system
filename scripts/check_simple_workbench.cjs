const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");

async function main() {
  const url = process.argv[2];
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(url || "")) throw new Error("需要本机测试地址");
  const output = "/tmp/quant-simple-workbench";
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
  });
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      page.setDefaultTimeout(10000);
      if (width < 700) await page.addInitScript(() => { delete window.EventSource; });
      await page.addInitScript(() => {
        window.__scanProgress = [];
        window.__paintedScanProgress = [];
        window.__paintedScanRows = [];
        function observeFrame() {
          const count = Number(document.querySelector("#scan-finished")?.textContent || 0);
          if (count && window.__paintedScanProgress.at(-1) !== count) {
            window.__paintedScanProgress.push(count);
            window.__paintedScanRows.push({ count, rows: document.querySelectorAll("#scan-results .scan-stock-row").length });
          }
          requestAnimationFrame(observeFrame);
        }
        requestAnimationFrame(observeFrame);
        new MutationObserver(() => {
          const count = Number(document.querySelector("#scan-finished")?.textContent || 0);
          if (count && window.__scanProgress.at(-1) !== count) window.__scanProgress.push(count);
          if (!window.__firstScanRow) window.__firstScanRow = document.querySelector('#scan-results .scan-stock-row[data-symbol="600002"]');
        }).observe(document, { childList: true, subtree: true, characterData: true });
      });
      const errors = [], calls = [];
      page.on("pageerror", error => errors.push(error.message));
      const captured = new Date().toISOString();
      const snapshot = {
        snapshot_id: "test-only", captured_at: captured,
        funds: { total_assets: "20000", market_value: "3100", cash_balance: "16900",
          available_cash: "16900", withdrawable_cash: "16900" },
        positions: [{ symbol: "600001", name: "测试持仓", quantity: 100, available: 0,
          price: "8", cost: "10", market_value: "800" },
        { symbol: "600004", name: "盈利示例", quantity: 100, available: 100, price: "13", cost: "10", market_value: "1300" },
        { symbol: "600005", name: "成本待核实", quantity: 100, available: 100, price: "10", cost: "0", market_value: "1000" }],
      };
      const connection = {
        request_token: "test-only", client: { code: "READY_TO_READ", version: "5.3.5" },
        capabilities: { read_account: true, watchlist_sync: true, launch: true },
        public_history_enabled: false, snapshot, stale: false, watchlist_stale: true, watchlist: null,
        risk: { snapshot_id: snapshot.snapshot_id, scope: "current_holdings", status: "LOCAL_ONLY",
          stock_exposure: .04, max_position_weight: .04, locked_weight: .04, history_curve: [] },
        holding_advice: { snapshot_id: snapshot.snapshot_id, items: [{
          symbol: "600001", action: "STOP_LOSS", status: "LOCAL_ONLY", holding_return: -.2,
          reason: "客户端显示亏损已超过默认亏损提醒线。",
          decision: "优先控制亏损", stop_loss_price: 9.2, profit_watch_price: 12, position_weight: .04,
          next_step: "核对最新价格及可卖数量。", industry: "货币金融服务", industry_source: "新浪行业分类",
          execution_note: "当前全部持仓不可卖；未提交订单。",
        }, { symbol: "600004", action: "NO_DATA", status: "LOCAL_ONLY", holding_return: .3,
          decision: "进入止盈观察", stop_loss_price: 9.2, profit_watch_price: 12, position_weight: .065,
          reason: "当前浮盈 30%，达到止盈观察线。", next_step: "等待趋势转弱确认，再评估分批减仓。",
          industry: "软件和信息技术服务业" },
        { symbol: "600005", action: "NO_DATA", status: "LOCAL_ONLY", holding_return: null,
          decision: "先核对持仓成本", stop_loss_price: null, profit_watch_price: null,
          reason: "成本无法计算有效收益率。", next_step: "重新核对客户端显示成本。",
          industry_state: "UNAVAILABLE" }] },
      };
      let scan = { running: false, status: "idle", top_results: [], scanned_count: 0, total_count: 0,
        failed_count: 0, skipped_count: 0, last_started_at: "", last_finished_at: "",
        version: 0, start_version: 0,
        pool: "gainers", universe_limit: 500, universe_source: "新浪公开涨幅榜", universe_fetched_at: "" };
      let starts = 0, failStatus = false, watchOnlyNext = false, allFailedNext = false;
      let unavailableNext = false, cachedNext = false;
      let holdScan = false, emptyPoolNext = false, failPoolNext = false;
      let refilledNext = false;
      let interruptedPoll = false;
      let deferStatus = false, captureDeferredStatus;
      let deferStart = false, captureDeferredStart;
      let deferAdvice = false, captureDeferredAdvice, noModelNext = false;
      const singleRequests = [];
      const algorithmNames = {
        robust_ensemble: "稳健集成模型（5日）", time_decay_knn: "时间衰减 KNN（5日）",
        knn: "KNN 相似样本", weighted_knn: "加权 KNN 相似样本", logistic_regression: "逻辑回归",
        random_forest: "随机森林（5日）", extra_trees: "极端随机树（5日）",
        gradient_boosting: "梯度提升树（5日）", gaussian_nb: "高斯朴素贝叶斯（5日）",
      };
      const pools = {
        gainers: ["新浪公开涨幅榜", 500], losers: ["新浪公开跌幅榜", 500],
        limit_down: ["东方财富公开跌停池", 100], limit_up: ["东方财富公开涨停池", 100],
      };
      const result = { symbol: "600002", name: "测试候选", action: "WATCH", score: 82, probability: .68,
        price: 12.5, analysis_date: "2026-09-30", reason: "买入确认不足，等待趋势与量能配合。", risk: "仍有回撤风险。",
        reasons: ["价格站上20日均线，但成交量尚未确认。"], evidence: ["20日均线12.00，收盘12.50。"],
        risks: ["短期涨幅较大，若不能放量突破，仍可能出现回撤。", "资金流未参与评分。"],
        observations: ["等待成交量放大后再评估。"],
        industry: "货币金融服务", industry_source: "新浪行业分类", universe_rank: 23, quote_source: "腾讯前复权" };
      const candidates = [result, ...Array.from({ length: 11 }, (_, i) => ({
        ...result, symbol: `6000${String(i + 10).padStart(2, "0")}`, name: `行业候选${i + 1}`,
        action: ["HOLD", "AVOID", "WATCH", "BUY"][i % 4],
        reason: ["信号中性，暂无明确买入理由。", "买入证据不足，暂不买入。", result.reason,
          "放量突破20日高点；MACD动能增强。"][i % 4],
        score: i === 10 ? 95 : 81 - i, industry: i % 2 ? "软件和信息技术服务业" : "医药制造业",
        universe_rank: 54 + i * 31,
      }))];
      function completionFrames() {
        return candidates.map((item, index) => ({ ...scan,
          version: scan.start_version + index + 1,
          running: index < candidates.length - 1, status: index < candidates.length - 1 ? "scanning" : "finished",
          top_results: candidates.slice(0, index + 1).map(stock => watchOnlyNext ? { ...stock, action: "WATCH" } : stock),
          scanned_count: index + 1, successful_count: index + 1,
          last_completed: `${item.name} ${item.symbol}`, industry_status: "行业已更新",
        }));
      }
      function finishScan() {
        if (scan.running && !holdScan) scan = { ...scan, running: false, status: "finished", scanned_count: scan.total_count,
          version: scan.start_version + scan.total_count + 1,
          successful_count: allFailedNext ? 0 : scan.total_count,
          last_finished_at: "2026-09-30 19:01:00",
          failed_count: allFailedNext ? scan.total_count : 0,
          status_message: allFailedNext ? "行情全部获取失败，本轮未形成有效分析" : "本轮筛选完成",
          top_results: allFailedNext || emptyPoolNext ? [] : [
            ...candidates.map((item, index) => ({ ...item, action: watchOnlyNext ? "WATCH" : item.action,
              universe_rank: scan.universe_limit === 100 ? 23 + index * 3 : item.universe_rank })),
            { ...result, symbol: "600003", name: "不应入选", action: "NO_DATA", score: 99 },
          ] };
        if (unavailableNext) scan = { ...scan, running: false, status: "data_unavailable",
          scanned_count: cachedNext ? 5 : 0, successful_count: cachedNext ? 5 : 0,
          failed_count: 0, deferred_count: scan.total_count - (cachedNext ? 5 : 0), status_message: "",
          top_results: cachedNext ? candidates.slice(0, 5).map(item => ({ ...item,
            quote_source: "腾讯前复权 · 本地缓存（采集 09-30 18:00）",
            risk: "本地缓存，非实时行情。",
          })) : [] };
      }
      await page.route("**/api/**", async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        calls.push(path);
        let payload;
        if (path === "/api/client") payload = { ok: true, connection };
        else if (path === "/api/client/sync") {
          assert.equal(request.headers()["x-desktop-token"], "test-only");
          snapshot.captured_at = new Date().toISOString();
          connection.stale = false;
          payload = { ok: true, connection };
        } else if (path === "/api/market-scan/start") {
          starts++;
          const { pool } = request.postDataJSON();
          assert.ok(pools[pool]);
          assert.deepEqual(request.postDataJSON(), { algorithm: "robust_ensemble", top: 10, pool });
          const [source, limit] = pools[pool];
          scan = { ...scan, pool, running: !failPoolNext, status: failPoolNext ? "error" : "scanning",
            last_started_at: "2026-09-30 19:00:00", start_version: starts * 100, version: starts * 100,
            top_results: [], scanned_count: 0, successful_count: 0,
            total_count: emptyPoolNext || failPoolNext ? 0 : refilledNext ? limit : 12,
            failed_count: 0, deferred_count: 0, status_message: failPoolNext ? "股票池读取失败" : "",
            fetched_count: emptyPoolNext || failPoolNext ? 0 : refilledNext ? limit + 90 : limit,
            skipped_count: emptyPoolNext || failPoolNext ? 0 : refilledNext ? 90 : limit - 12,
            universe_shortfall: failPoolNext || refilledNext ? 0 : emptyPoolNext ? limit : limit - 12,
            universe_limit: limit, universe_source: source, universe_date: limit === 100 ? "2026-09-30" : "",
            universe_fetched_at: "2026-09-30 19:00:01" };
          // Given 缓存扫描早于启动响应完成 Then 也必须从第一只开始绘制，不先闪现最终榜单。
          if (starts === 1 || unavailableNext) finishScan();
          if (deferStart) {
            deferStart = false;
            captureDeferredStart({ route, oldSnapshot: { ...scan } });
            return;
          }
          payload = { ok: true, snapshot: scan };
        } else if (path === "/api/market-scan/status") {
          if (failStatus) return route.abort("failed");
          if (deferStatus) {
            deferStatus = false;
            captureDeferredStatus({ route, oldSnapshot: { ...scan } });
            return;
          }
          finishScan();
          const after = new URL(request.url()).searchParams.get("after");
          if (starts === 2 && Number(after) === scan.start_version + 4 && !interruptedPoll) {
            interruptedPoll = true;
            return route.abort("failed");
          }
          const next = after !== null && starts <= 2
            ? completionFrames().find(frame => frame.version > Number(after)) : null;
          payload = { ok: true, snapshot: next || scan };
        } else if (path === "/api/market-scan/stream") {
          finishScan();
          const frames = starts === 1 ? completionFrames()
            : starts === 2 ? completionFrames().slice(0, 3) : [scan];
          if (starts === 1) frames.splice(1, 0, frames[0]);
          if (scan.pool !== "gainers") frames.unshift({ ...scan, pool: "gainers",
            start_version: scan.start_version - 100, running: false, status: "finished",
            top_results: [{ ...result, name: "旧股票池污染" }], scanned_count: 99 });
          return route.fulfill({ contentType: "text/event-stream",
            body: frames.map(snapshot => `data: ${JSON.stringify({ ok: true, snapshot })}\n\n`).join("") });
        } else if (path === "/api/advise") {
          assert.equal(request.postDataJSON().symbol, result.symbol);
          const { algorithm } = request.postDataJSON();
          assert.ok(algorithmNames[algorithm]);
          singleRequests.push(algorithm);
          payload = { ok: true, advice: {
            action: result.action, score: 82, reasons: [result.reason], evidence: [], risks: [result.risk], observations: [],
            indicators: { symbol: result.symbol, name: result.name, analysis_date: result.analysis_date,
              close: 12.5, return_1d: .01, return_5d: .03, volume_ratio_20d: 1.2, ma250: 10,
              macd_dif: .2, macd_dea: .1, macd_histogram: .2, first_day_high: 0 },
            ml_prediction: noModelNext ? null : { algorithm, algorithm_name: algorithmNames[algorithm],
              horizon_days: ["knn", "weighted_knn", "logistic_regression"].includes(algorithm) ? 2 : 5,
              buy_probability: .68, sample_count: 100,
              neighbor_count: ["knn", "weighted_knn", "time_decay_knn", "robust_ensemble"].includes(algorithm) ? 10 : 0,
              positive_count: ["knn", "weighted_knn", "time_decay_knn", "robust_ensemble"].includes(algorithm) ? 7 : 0 },
          }, beginner: { headline: "满足观察条件" },
          chart: { dates: ["2026-09-29", "2026-09-30"], closes: [12, 12.5], volumes: [100, 120] } };
          if (deferAdvice) {
            deferAdvice = false;
            captureDeferredAdvice({ route, payload });
            return;
          }
        } else if (path === "/api/sell-advice") {
          assert.equal(request.postDataJSON().cost_price, null, "候选详情不可带入另一只持仓的成本");
          payload = { ok: true, sell_advice: { action: "NO_POSITION", sell_risk_score: 10,
            holding_return: null, reasons: [], risks: [], observations: [] } };
        } else throw new Error(`意外接口：${path}`);
        await route.fulfill({ contentType: "application/json", body: JSON.stringify(payload) });
      });
      async function navigate(name) {
        if (width < 700) await page.locator("#menu-button").click();
        await page.locator(`.nav-item[data-page="${name}"]`).click();
      }
      // Given 旧连接链接 When 打开 Then 归入设置且保留总览、行情、交易、自动化和设置。
      await page.goto(`${url}/#client`);
      await page.locator("#settings-page.active").waitFor();
      assert.deepEqual(await page.locator(".nav-item[data-page]").evaluateAll(nodes => nodes.map(n => n.dataset.page)),
        ["overview", "market", "trade", "automation", "settings"]);
      assert.deepEqual(await page.locator("[data-page-panel]").evaluateAll(nodes => nodes.map(n => n.dataset.pagePanel)),
        ["overview", "market", "trade", "automation", "settings"]);
      assert.equal(await page.locator("#client-public-history").isChecked(), false);
      assert.equal(starts, 0);

      // Given 已同步持仓 When 看总览 Then 直接显示卖出建议及不可卖约束。
      await navigate("overview");
      await page.locator(".holding-action").filter({ hasText: "控制亏损" }).waitFor();
      assert.equal(await page.locator(".holding-decision").count(), 3);
      assert.deepEqual(await page.locator(".holding-action").allTextContents(),
        ["优先控制亏损", "进入止盈观察", "先核对持仓成本"]);
      assert.match(await page.locator("#holding-decisions").innerText(), /不可卖/);
      assert.match(await page.locator("#holding-decisions").innerText(), /9\.20/);
      assert.match(await page.locator("#holding-decisions").innerText(), /软件和信息技术服务业/);
      assert.equal(await page.locator(".holding-levels").last().locator("dd").first().innerText(), "--");
      await page.screenshot({ path: `${output}/overview-${width}.png`, fullPage: true, animations: "disabled" });

      // Given 前五只有不同分析结论 When 逐股完成 Then 有几只有效结果就显示几行，超过十只保留Top10。
      await navigate("market");
      await page.locator("#scan-finished").filter({ hasText: "12" }).waitFor();
      assert.equal(starts, 1);
      assert.equal(calls.includes("/api/market-scan/stream"), width >= 700);
      assert.equal(await page.locator("#scan-results .scan-stock-row").count(), 10);
      assert.match(await page.locator("#scan-results .scan-stock-row").first().innerText(), /行业候选11/);
      assert.equal(await page.locator("#scan-results .stock-industry").count(), 10);
      assert.match(await page.locator("#scan-meta").innerText(), /涨幅 Top 500/);
      assert.match(await page.locator("#scan-universe-status").innerText(), /新浪公开涨幅榜/);
      assert.match(await page.locator("#scan-universe-status").innerText(), /500 只/);
      assert.match(await page.locator("#scan-universe-status").innerText(), /板块排除 488/);
      assert.match(await page.locator("#scan-universe-status").innerText(), /目标 500 只.*合格入池 12 只/);
      assert.match(await page.locator("#scan-coverage-note").innerText(), /已遍历本类股票池.*少 488 只/);
      assert.equal(await page.locator("#scan-successful").innerText(), "12");
      assert.match(await page.locator("#scan-universe-status").innerText(), /非同花顺客户端直读/);
      assert.match(await page.locator('#scan-results .scan-stock-row[data-symbol="600002"]').innerText(), /涨幅榜 #23/);
      // Given 缓存结果密集到达或浏览器不支持SSE When 逐股更新 Then 每个中间榜单都有独立绘制帧。
      await page.waitForFunction(() => window.__paintedScanProgress.at(-1) === 12);
      assert.deepEqual(await page.evaluate(() => window.__scanProgress), Array.from({ length: 12 }, (_, i) => i + 1));
      assert.deepEqual(await page.evaluate(() => window.__paintedScanProgress), Array.from({ length: 12 }, (_, i) => i + 1));
      assert.deepEqual(await page.evaluate(() => window.__paintedScanRows),
        Array.from({ length: 12 }, (_, i) => ({ count: i + 1, rows: Math.min(i + 1, 10) })));
      assert.match(await page.locator('#scan-results .scan-stock-row[data-symbol="600002"] .scan-action').innerText(), /建议继续观察/);
      // Given 精简后的核心理由 When 逐股渲染 Then 直接展示短句，不重新拼接模型样本和评分解释。
      assert.equal(await page.locator('#scan-results .scan-stock-row[data-symbol="600002"] td').nth(5).innerText(), result.reason);
      assert.ok(result.reason.length <= 40);
      assert.equal(await page.locator('#scan-results .scan-stock-row[data-symbol="600013"] td').nth(5).innerText(),
        "放量突破20日高点；MACD动能增强。");
      assert.match(await page.locator('#scan-results .scan-stock-row[data-symbol="600011"] .scan-action').innerText(), /暂不建议买进/);
      assert.equal(await page.evaluate(() => window.__firstScanRow === document.querySelector('#scan-results .scan-stock-row[data-symbol="600002"]')), true);
      assert.doesNotMatch(await page.locator("#scan-results").innerText(), /不应入选/);
      assert.equal(await page.locator("#advisor-form").isVisible(), false);
      assert.equal(await page.getByRole("tablist", { name: "候选股票池" }).getByRole("tab").count(), 4);
      assert.equal(await page.locator("#scan-auto-button").isEnabled(), false);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: `${output}/market-${width}.png`, fullPage: true, animations: "disabled" });

      // Given 榜单精简风险 When 点击详情 Then 在当前行下展开完整分析，不跳转或重新请求单股分析。
      const stockRow = page.locator('#scan-results .scan-stock-row[data-symbol="600002"]');
      const toggle = stockRow.getByRole("button");
      assert.equal(await stockRow.locator("td").nth(6).innerText(), result.risk);
      assert.equal(await toggle.getAttribute("aria-expanded"), "false");
      const detailId = await toggle.getAttribute("aria-controls");
      const details = page.locator(`[id="${detailId}"]`);
      assert.equal(await details.isVisible(), false);
      const callsBeforeDetails = calls.length;
      await toggle.click();
      assert.equal(await toggle.getAttribute("aria-expanded"), "true");
      assert.equal(await details.isVisible(), true);
      for (const text of [...result.reasons, ...result.evidence, ...result.risks, ...result.observations, result.quote_source]) {
        assert.ok((await details.innerText()).includes(text));
      }
      assert.equal(await stockRow.evaluate((row, id) => row.nextElementSibling.id === id, detailId), true);
      assert.equal(await page.locator("#scan-tab").evaluate(node => node.classList.contains("active")), true);
      assert.equal(await page.locator("#advisor-form").isVisible(), false);
      assert.equal(page.url(), `${url}/#market`);
      assert.equal(calls.length, callsBeforeDetails, "展开不得触发分析或行情请求");
      assert.equal(singleRequests.length, 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await details.scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${output}/market-details-${width}.png`, fullPage: false, animations: "disabled" });

      // Given 详情已展开 When 同一轮刷新并重新排序 Then 保留展开状态、按钮焦点且详情始终紧随所属股票。
      await toggle.focus();
      await page.evaluate(state => renderMarketScan(state), { ...scan, top_results: scan.top_results.map(item =>
        item.symbol === result.symbol ? { ...item, score: 99, risks: ["更新后的完整风险。"] } : item) });
      assert.equal(await page.locator("#scan-results .scan-stock-row").first().getAttribute("data-symbol"), result.symbol);
      assert.equal(await toggle.getAttribute("aria-expanded"), "true");
      assert.match(await details.innerText(), /更新后的完整风险/);
      assert.equal(await stockRow.evaluate((row, id) => row.nextElementSibling.id === id, detailId), true);
      assert.equal(await toggle.evaluate(node => node === document.activeElement), true);
      await toggle.press("Enter");
      assert.equal(await details.isVisible(), false);
      assert.equal(await toggle.getAttribute("aria-expanded"), "false");
      await toggle.press("Space");
      assert.equal(await details.isVisible(), true);

      // Given 股票离开Top10 When 更新榜单 Then 同时移除展开详情，重入榜单默认折叠。
      await page.evaluate(state => renderMarketScan(state), { ...scan,
        top_results: scan.top_results.filter(item => item.symbol !== result.symbol) });
      assert.equal(await details.count(), 0);
      await page.evaluate(state => renderMarketScan(state), scan);
      assert.equal(await toggle.getAttribute("aria-expanded"), "false");
      await toggle.click();
      assert.equal(await details.isVisible(), true);

      // Given 旧接口未携带详情列表 When 展开 Then 回退展示现有摘要，内容按文本渲染。
      const legacy = { ...result, reasons: undefined, risks: undefined, evidence: undefined, observations: undefined,
        reason: "<img src=x onerror=alert(1)>旧版理由", risk: "旧版完整风险" };
      await page.evaluate(state => renderMarketScan(state), { ...scan,
        top_results: scan.top_results.map(item => item.symbol === result.symbol ? legacy : item) });
      assert.ok((await details.innerText()).includes(legacy.reason));
      assert.match(await details.innerText(), /旧版完整风险/);
      assert.equal(await details.locator("img").count(), 0);
      await page.evaluate(state => renderMarketScan(state), scan);

      await page.locator("#single-tab").click();
      await page.locator("#symbol").fill(result.symbol);
      await page.locator("#submit-button").click();
      await page.locator("#stock-name").filter({ hasText: "测试候选" }).waitFor();
      await page.locator("#advice-view:not(.hidden)").waitFor();

      // Given 单股分析 When 选择九种算法 Then 每次请求与展示的算法一致，切换自动重算且控件在手机上不溢出。
      assert.equal(await page.locator("#algorithm").isVisible(), true);
      assert.deepEqual(new Set(await page.locator("#algorithm option").evaluateAll(nodes => nodes.map(n => n.value))),
        new Set(Object.keys(algorithmNames)));
      for (const algorithm of Object.keys(algorithmNames).filter(key => key !== "robust_ensemble")) {
        await page.locator("#algorithm").selectOption(algorithm);
        await page.locator("#advice-view:not(.hidden) #ml-detail").filter({ hasText: algorithmNames[algorithm] }).waitFor();
        assert.equal(singleRequests.at(-1), algorithm);
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.equal(await page.locator("#algorithm").evaluate(node => node.getBoundingClientRect().right <= innerWidth), true);

      // Given 老算法响应未完成 When 快速换算法 Then 新结果不能被迟到响应覆盖。
      deferAdvice = true;
      const oldAdvicePromise = new Promise(resolve => { captureDeferredAdvice = resolve; });
      await page.locator("#algorithm").selectOption("random_forest");
      const oldAdvice = await oldAdvicePromise;
      assert.equal(await page.locator("#advice-view").isVisible(), false);
      await page.locator("#algorithm").selectOption("gradient_boosting");
      await page.locator("#advice-view:not(.hidden) #ml-detail").filter({ hasText: "梯度提升树" }).waitFor();
      await oldAdvice.route.fulfill({ contentType: "application/json", body: JSON.stringify(oldAdvice.payload) });
      assert.match(await page.locator("#ml-detail").innerText(), /梯度提升树/);
      assert.doesNotMatch(await page.locator("#ml-detail").innerText(), /随机森林/);
      assert.doesNotMatch(await page.locator("#ml-detail").innerText(), /最近邻样本数/);
      await page.screenshot({ path: `${output}/single-algorithms-${width}.png`, fullPage: true, animations: "disabled" });

      // Given 所选模型样本不足 When 返回空模型 Then 给出可恢复错误，不复用旧算法结果也不崩溃。
      noModelNext = true;
      await page.locator("#algorithm").selectOption("gaussian_nb");
      await page.locator("#error-state").filter({ hasText: "样本不足" }).waitFor();
      assert.equal(await page.locator("#advice-view").isVisible(), false);
      assert.equal(await page.locator("#submit-button").isEnabled(), true);
      noModelNext = false;
      await page.locator("#submit-button").click();
      await page.locator("#advice-view:not(.hidden) #ml-detail").filter({ hasText: "高斯朴素贝叶斯" }).waitFor();

      // Given 单股请求进行中 When 返回榜单 Then 晚到结果不能覆盖榜单页面。
      deferAdvice = true;
      const leftAdvicePromise = new Promise(resolve => { captureDeferredAdvice = resolve; });
      await page.locator("#algorithm").selectOption("extra_trees");
      const leftAdvice = await leftAdvicePromise;
      await page.locator("#scan-tab").click();
      await leftAdvice.route.fulfill({ contentType: "application/json", body: JSON.stringify(leftAdvice.payload) });
      assert.equal(await page.locator("#scan-view").isVisible(), true);
      assert.equal(await page.locator("#advice-view").isVisible(), false);
      assert.equal(starts, 1, "切换详情不重复发起筛选");

      // Given 实时通知断开且本轮只有观察结论 When 降级轮询 Then 逐只入榜并保留观察标签，不冒充买入建议。
      watchOnlyNext = true;
      await page.evaluate(() => { window.__scanProgress = []; window.__paintedScanProgress = []; });
      await page.locator("#scan-once-button").click();
      await page.waitForFunction(() => document.querySelector("#scan-finished").textContent === "12"
        && document.querySelectorAll('#scan-results .scan-action[data-action="WATCH"]').length === 10);
      await page.waitForFunction(() => window.__paintedScanProgress.at(-1) === 12);
      const progress = await page.evaluate(() => window.__scanProgress);
      assert.deepEqual(progress.slice(progress.indexOf(1)), Array.from({ length: 12 }, (_, i) => i + 1),
        "断线后应从已收到的游标继续，非买入结果也逐股刷新");
      assert.equal(interruptedPoll, true, "降级请求临时失败后仍应补全逐股事件");
      assert.equal(await page.locator("#scan-results .scan-stock-row").count(), 10);
      assert.equal(await page.locator("#scan-empty").isVisible(), false);
      assert.equal(starts, 2);
      assert.equal(await page.locator('#scan-results [aria-expanded="true"]').count(), 0,
        "新一轮分析必须清除上一轮的展开状态");

      // Given 全部行情失败 When 本轮结束 Then 明确未形成有效分析而非正常无候选。
      allFailedNext = true;
      await page.locator("#scan-once-button").click();
      await page.locator("#scan-empty").filter({ hasText: "全部获取失败" }).waitFor();
      assert.match(await page.locator("#scan-refresh-status").innerText(), /未形成有效分析/);
      assert.equal(await page.locator("#scan-successful").innerText(), "0", "失败股票不计入成功分析数");
      assert.equal(await page.locator("#scan-results .scan-stock-row").count(), 0);
      allFailedNext = false;

      // Given 两个行情源暂停且无有效缓存 When 本轮结束 Then 显示500只待重试，不冒充500只分析失败。
      unavailableNext = refilledNext = true;
      await page.locator("#scan-once-button").click();
      await page.locator("#scan-empty").filter({ hasText: "在线行情源暂不可用" }).waitFor();
      assert.equal(await page.locator("#scan-deferred").innerText(), "500");
      assert.equal(await page.locator("#scan-failed").innerText(), "0");
      assert.equal(await page.locator("#scan-finished").innerText(), "0");
      assert.equal(await page.locator("#scan-results .scan-stock-row").count(), 0);
      assert.match(await page.locator("#scan-refresh-status").innerText(), /待重试/);
      assert.equal(await page.locator("#scan-once-button").isEnabled(), true);
      assert.equal(await page.locator("#scan-auto-button").isEnabled(), false);

      // Given 五只有可验证缓存其余暂不可用 When 回放结果 Then 保留五行并直接展示缓存来源与采集时间。
      cachedNext = true;
      await page.locator("#scan-once-button").click();
      await page.locator("#scan-deferred").filter({ hasText: "495" }).waitFor();
      assert.equal(await page.locator("#scan-results .scan-stock-row").count(), 5);
      assert.equal(await page.locator("#scan-successful").innerText(), "5");
      assert.equal(await page.locator("#scan-failed").innerText(), "0");
      assert.match(await page.locator(".stock-quote-source").first().innerText(), /本地缓存.*采集 09-30 18:00/);
      assert.equal(await page.locator(".stock-quote-source").first().isVisible(), true);
      assert.equal(await page.locator("#scan-empty").isVisible(), false);
      assert.equal(await page.locator("#scan-once-button").isEnabled(), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: `${output}/market-cached-${width}.png`, fullPage: true, animations: "disabled" });

      // Given 行情源恢复 When 手动重试 Then 清零待重试数量并正常显示新榜单。
      unavailableNext = cachedNext = refilledNext = false;
      await page.locator("#scan-once-button").click();
      await page.locator("#scan-results").filter({ hasText: "行业候选11" }).waitFor();
      assert.equal(await page.locator("#scan-deferred").innerText(), "0");
      assert.doesNotMatch(await page.locator("#scan-refresh-status").innerText(), /待重试/);
      assert.doesNotMatch(await page.locator("#scan-results").innerText(), /本地缓存/);

      // Given 历史候选存在但状态读取失败 When 返回候选页 Then 清除旧推荐并允许重试。
      watchOnlyNext = false;
      await page.locator("#scan-once-button").click();
      await page.locator("#scan-results").filter({ hasText: "测试候选" }).waitFor();

      // Given 涨幅榜已有结果 When 选择跌幅池 Then 清空旧榜并允许分析中切换。
      await page.locator('#scan-results .scan-stock-row[data-symbol="600002"] button').click();
      assert.equal(await details.isVisible(), true);
      await page.locator("#single-tab").click();
      deferStatus = true;
      const deferred = new Promise(resolve => { captureDeferredStatus = resolve; });
      await page.locator("#scan-tab").click();
      const { route: oldStatus, oldSnapshot } = await deferred;
      holdScan = true;
      await page.getByRole("tab", { name: "跌幅 500", exact: true }).click();
      await page.waitForFunction(() => document.querySelector("#scan-auto-button").disabled === false
        && document.querySelector("#pool-losers").disabled === false);
      assert.equal(await page.locator("#scan-results .scan-stock-row").count(), 0);
      assert.equal(await page.locator("#scan-results .scan-detail-row").count(), 0, "切池清除所有旧详情");
      assert.equal(await page.locator("#pool-losers").getAttribute("aria-selected"), "true");
      assert.match(await page.locator("#scan-universe-status").innerText(), /新浪公开跌幅榜/);
      assert.match(await page.locator("#scan-scope").innerText(), /升序选取 500/);
      await oldStatus.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, snapshot: oldSnapshot }) });
      await page.waitForFunction(() => document.querySelector("#pool-losers").getAttribute("aria-selected") === "true");
      assert.equal(await page.locator("#scan-results .scan-stock-row").count(), 0, "迟到的旧轮询不能恢复涨幅榜");

      // Given 正在分析跌幅500 When 切换空跌停池 Then 清空进度且按实际零只显示。
      holdScan = false;
      emptyPoolNext = true;
      await page.getByRole("tab", { name: "跌停 100", exact: true }).click();
      await page.locator("#scan-empty").filter({ hasText: "当日股票池为空" }).waitFor();
      assert.equal(await page.locator("#scan-results .scan-stock-row").count(), 0);
      assert.equal(await page.locator("#scan-total").innerText(), "0");
      assert.match(await page.locator("#scan-universe-status").innerText(), /东方财富公开跌停池.*股票池日期：2026-09-30/);

      // Given 选择涨停池 When 完成 Then 原涨幅池推送被隔离，Top10与行业仍保留。
      emptyPoolNext = false;
      refilledNext = true;
      await page.getByRole("tab", { name: "涨停 100", exact: true }).click();
      await page.locator("#scan-results").filter({ hasText: "测试候选" }).waitFor();
      assert.equal(await page.locator("#scan-results .scan-stock-row").count(), 10);
      assert.equal(await page.locator('#scan-results [aria-expanded="true"]').count(), 0);
      assert.doesNotMatch(await page.locator("#scan-results").innerText(), /旧股票池污染/);
      assert.match(await page.locator('#scan-results .scan-stock-row[data-symbol="600002"]').innerText(), /涨停池 #23/);
      assert.match(await page.locator("#scan-universe-status").innerText(), /东方财富公开涨停池/);
      assert.match(await page.locator("#scan-universe-status").innerText(), /目标 100 只.*合格入池 100 只.*排名检查 190 只.*板块排除 90 只/);
      assert.equal(await page.locator("#scan-successful").innerText(), "100");
      assert.equal(await page.locator("#scan-total").innerText(), "100");
      assert.equal(await page.locator("#scan-coverage-note").isVisible(), false);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      for (const tab of await page.locator("[data-market-pool]").all()) {
        assert.equal(await tab.evaluate(node => node.scrollWidth <= node.clientWidth), true);
      }
      await page.screenshot({ path: `${output}/market-pools-${width}.png`, fullPage: true, animations: "disabled" });

      // Given 启动响应尚未返回 When 连续选择其他TAB Then 立即刷新选择且只启动最后一个，不被旧响应覆盖。
      deferStart = true;
      const pendingStart = new Promise(resolve => { captureDeferredStart = resolve; });
      const startsBeforeSwitch = starts;
      await page.getByRole("tab", { name: "跌幅 500", exact: true }).click();
      const { route: oldStart, oldSnapshot: oldStartSnapshot } = await pendingStart;
      assert.equal(await page.locator("#pool-gainers").isEnabled(), true, "启动期间不能锁住其他TAB");
      await page.getByRole("tab", { name: "涨幅 500", exact: true }).click();
      await page.getByRole("tab", { name: "跌停 100", exact: true }).click();
      assert.equal(await page.locator("#pool-limit-down").getAttribute("aria-selected"), "true");
      assert.equal(await page.locator("#scan-results .scan-stock-row").count(), 0);
      assert.equal(await page.locator("#scan-finished").innerText(), "0");
      emptyPoolNext = true;
      await oldStart.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, snapshot: oldStartSnapshot }) });
      await page.locator("#scan-empty").filter({ hasText: "当日股票池为空" }).waitFor();
      assert.equal(starts, startsBeforeSwitch + 2, "中间选择的涨幅池不应启动");
      assert.equal(await page.locator("#pool-limit-down").getAttribute("aria-selected"), "true");
      emptyPoolNext = false;

      // Given 所选池响应失败 When 更新 Then 报告池读取失败而非沿用其他榜单。
      failPoolNext = true;
      await page.locator("#scan-once-button").click();
      await page.locator("#scan-empty").filter({ hasText: "所选股票池读取失败" }).waitFor();
      assert.equal(await page.locator("#scan-results .scan-stock-row").count(), 0);
      failPoolNext = false;
      await page.locator("#scan-once-button").click();
      await page.locator("#scan-results").filter({ hasText: "测试候选" }).waitFor();
      await page.locator("#single-tab").click();
      failStatus = true;
      await page.locator("#scan-tab").click();
      await page.locator("#scan-empty").filter({ hasText: "读取失败" }).waitFor();
      assert.equal(await page.locator("#scan-results .scan-stock-row").count(), 0);

      // Given 账户快照过期 When 返回总览 Then 不再将旧结论展示为当前卖出建议。
      snapshot.captured_at = new Date(Date.now() - 61000).toISOString();
      connection.stale = true;
      await page.locator("#client-auto").evaluate(node => { node.checked = false; });
      await navigate("overview");
      await page.locator(".holding-action").filter({ hasText: "需重新同步" }).first().waitFor();
      assert.doesNotMatch((await page.locator(".holding-action").allTextContents()).join(" "), /止损|控制亏损/);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ width, navigation: true, top10: true, candidatePools: true, industry: true, perStockUpdates: true, perFrameUpdates: true, holdings: true,
        staleAndFailure: true, sourceOutageAndCache: true, singleAlgorithms: true, inlineDetails: true, starts, errors }));
      await page.close();
    }
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
