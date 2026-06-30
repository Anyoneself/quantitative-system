const form = document.querySelector("#advisor-form");
const scanForm = document.querySelector("#scan-form");
const symbolInput = document.querySelector("#symbol");
const algorithmInput = document.querySelector("#algorithm");
const costPriceInput = document.querySelector("#cost-price");
const quantityInput = document.querySelector("#quantity");
const maxLossRateInput = document.querySelector("#max-loss-rate");
const targetProfitRateInput = document.querySelector("#target-profit-rate");
const scanAlgorithmInput = document.querySelector("#scan-algorithm");
const button = document.querySelector("#submit-button");
const scanAutoButton = document.querySelector("#scan-auto-button");
const singleTab = document.querySelector("#single-tab");
const scanTab = document.querySelector("#scan-tab");
const emptyState = document.querySelector("#empty-state");
const loadingState = document.querySelector("#loading-state");
const errorState = document.querySelector("#error-state");
const adviceView = document.querySelector("#advice-view");
const scanView = document.querySelector("#scan-view");
let marketEventSource = null;

const actionText = {
  BUY: "建议买入观察",
  WATCH: "建议继续观察",
  HOLD: "信号中性",
  AVOID: "暂不建议买进",
  NO_DATA: "数据不足",
};

singleTab.addEventListener("click", () => setMode("single"));
scanTab.addEventListener("click", () => setMode("scan"));

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setState("loading");
  button.disabled = true;

  try {
    const response = await fetch("/api/advise", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: symbolInput.value.trim(),
        algorithm: algorithmInput.value,
      }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "分析失败");
    }
    renderAdvice(payload);
    await loadSellAdvice();
    setState("result");
  } catch (error) {
    errorState.textContent = error.message;
    setState("error");
  } finally {
    button.disabled = false;
  }
});

scanForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await startMarketScan();
});

scanAutoButton.addEventListener("click", async () => {
  await stopMarketScan();
});

function setMode(mode) {
  const isScan = mode === "scan";
  singleTab.classList.toggle("active", !isScan);
  scanTab.classList.toggle("active", isScan);
  form.classList.toggle("hidden", isScan);
  scanForm.classList.toggle("hidden", !isScan);
  if (isScan) {
    setState("scan");
    refreshMarketScanStatus();
    startMarketUpdates();
    return;
  }
  stopMarketUpdates();
  setState("empty");
}

async function loadSellAdvice() {
  const response = await fetch("/api/sell-advice", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol: symbolInput.value.trim(),
      cost_price: parseOptionalNumber(costPriceInput.value),
      quantity: parseOptionalNumber(quantityInput.value),
      max_loss_rate: parseOptionalNumber(maxLossRateInput.value) || 0.08,
      target_profit_rate: parseOptionalNumber(targetProfitRateInput.value) || 0.20,
    }),
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || "卖出判断失败");
  }
  renderSellAdvice(payload.sell_advice);
}

function renderSellAdvice(sellAdvice) {
  document.querySelector("#sell-score").textContent = sellAdvice.sell_risk_score;
  document.querySelector("#sell-action-label").textContent = sellActionText(sellAdvice.action);
  const detail = document.querySelector("#sell-detail");
  detail.innerHTML = "";
  const rows = [
    ["卖出建议", sellActionText(sellAdvice.action)],
    ["卖出风险评分", String(sellAdvice.sell_risk_score)],
  ];
  if (sellAdvice.holding_return !== null && sellAdvice.holding_return !== undefined) {
    rows.push(["持仓收益率", formatPercent(sellAdvice.holding_return)]);
  }
  if (sellAdvice.key_levels) {
    rows.push(["20 日均线", formatNumber(sellAdvice.key_levels.ma20)]);
    rows.push(["年线 MA250", sellAdvice.key_levels.ma250 > 0 ? formatNumber(sellAdvice.key_levels.ma250) : "数据不足"]);
    rows.push(["中枢下沿", sellAdvice.key_levels.chan_center_lower !== null ? formatNumber(sellAdvice.key_levels.chan_center_lower) : "未形成"]);
  }
  rows.forEach(([label, value]) => {
    const row = document.createElement("div");
    const labelNode = document.createElement("span");
    const valueNode = document.createElement("strong");
    labelNode.textContent = label;
    valueNode.textContent = value;
    row.append(labelNode, valueNode);
    detail.appendChild(row);
  });
  renderList("#sell-risks", [...(sellAdvice.reasons || []), ...(sellAdvice.risks || []), ...(sellAdvice.observations || [])]);
}

function sellActionText(action) {
  const actionMap = {
    HOLD: "继续持有",
    REDUCE: "减仓观察",
    TAKE_PROFIT: "止盈/分批减仓",
    STOP_LOSS: "止损",
    NO_POSITION: "仅技术风险提示",
  };
  return actionMap[action] || action;
}

function setState(state) {
  emptyState.classList.toggle("hidden", state !== "empty");
  loadingState.classList.toggle("hidden", state !== "loading");
  errorState.classList.toggle("hidden", state !== "error");
  adviceView.classList.toggle("hidden", state !== "result");
  scanView.classList.toggle("hidden", state !== "scan");
}

function renderAdvice(payload) {
  const advice = payload.advice;
  const indicators = advice.indicators;
  const prediction = advice.ml_prediction;
  const beginner = payload.beginner;
  const chan = payload.chan;
  const fundFlow = payload.fund_flow;
  const divergence = payload.divergence;

  document.querySelector("#stock-code").textContent = indicators.symbol;
  document.querySelector("#stock-name").textContent = indicators.name || "未知";
  document.querySelector("#stock-date").textContent = indicators.analysis_date;
  document.querySelector("#stock-link").href = `https://stockpage.10jqka.com.cn/${indicators.symbol}/`;
  document.querySelector("#headline").textContent =
    `${actionText[advice.action] || advice.action}：${beginner.headline}`;
  document.querySelector("#score").textContent = advice.score;
  document.querySelector("#buy-action-label").textContent = actionText[advice.action] || advice.action;
  document.querySelector("#close-price").textContent = formatNumber(indicators.close);
  document.querySelector("#return-1d").textContent = formatPercent(indicators.return_1d);
  document.querySelector("#return-5d").textContent = formatPercent(indicators.return_5d);
  document.querySelector("#volume-ratio").textContent = indicators.volume_ratio_20d.toFixed(2);
  document.querySelector("#ma250").textContent = indicators.ma250 > 0 ? formatNumber(indicators.ma250) : "数据不足";
  document.querySelector("#macd-summary").textContent = `${indicators.macd_dif >= indicators.macd_dea ? "多头" : "空头"} ${indicators.macd_histogram.toFixed(3)}`;
  document.querySelector("#first-day-high").textContent = indicators.first_day_high > 0 ? formatNumber(indicators.first_day_high) : "不适用";
  document.querySelector("#data-range").textContent =
    `${payload.chart.dates[payload.chart.dates.length - 1]} · ${payload.chart.dates.length} 个交易日`;

  renderList("#reasons", advice.reasons);
  renderList("#evidence", advice.evidence || []);
  renderList("#risks", advice.risks);
  renderList("#observations", advice.observations);
  renderMl(prediction);
  renderChan(chan);
  renderFundFlow(fundFlow);
  renderDivergence(divergence);
  drawStockChart(payload.chart, chan);
}

function renderDivergence(divergence) {
  if (!divergence) {
    document.querySelector("#divergence-adjustment").textContent = "未参与评分";
    document.querySelector("#divergence-signal").textContent = "样本不足";
    document.querySelector("#divergence-confidence").textContent = "--";
    document.querySelector("#divergence-buy").textContent = "--";
    document.querySelector("#divergence-sell").textContent = "--";
    document.querySelector("#divergence-explanation").textContent = "背驰样本不足，本次评分未纳入背驰信号。";
    return;
  }
  const buySign = divergence.bullish_score_adjustment > 0 ? "+" : "";
  const sellSign = divergence.bearish_risk_adjustment > 0 ? "+" : "";
  document.querySelector("#divergence-adjustment").textContent = `买入 ${buySign}${divergence.bullish_score_adjustment} / 卖出 ${sellSign}${divergence.bearish_risk_adjustment}`;
  document.querySelector("#divergence-signal").textContent = divergence.signal;
  document.querySelector("#divergence-confidence").textContent = String(divergence.confidence);
  document.querySelector("#divergence-buy").textContent = `${buySign}${divergence.bullish_score_adjustment}`;
  document.querySelector("#divergence-sell").textContent = `${sellSign}${divergence.bearish_risk_adjustment}`;
  document.querySelector("#divergence-explanation").textContent = divergence.reasons.join("；");
}

function renderFundFlow(fundFlow) {
  if (!fundFlow) {
    document.querySelector("#fund-flow-adjustment").textContent = "未参与评分";
    document.querySelector("#fund-flow-signal").textContent = "不可用";
    document.querySelector("#fund-flow-latest").textContent = "--";
    document.querySelector("#fund-flow-5d").textContent = "--";
    document.querySelector("#fund-flow-10d").textContent = "--";
    document.querySelector("#fund-flow-explanation").textContent = "资金流数据暂不可用，本次评分未纳入资金流。";
    return;
  }
  const sign = fundFlow.fund_flow_score_adjustment > 0 ? "+" : "";
  document.querySelector("#fund-flow-adjustment").textContent = `资金流调整 ${sign}${fundFlow.fund_flow_score_adjustment}`;
  document.querySelector("#fund-flow-signal").textContent = fundFlow.signal;
  document.querySelector("#fund-flow-latest").textContent = formatAmount(fundFlow.latest_main_net_inflow);
  document.querySelector("#fund-flow-5d").textContent = formatAmount(fundFlow.main_inflow_5d);
  document.querySelector("#fund-flow-10d").textContent = formatAmount(fundFlow.main_inflow_10d);
  document.querySelector("#fund-flow-explanation").textContent = fundFlow.explanation;
}

async function startMarketScan() {
  setState("scan");
  const scanButton = document.querySelector("#scan-once-button");
  scanButton.disabled = true;

  try {
    const response = await fetch("/api/market-scan/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        algorithm: scanAlgorithmInput.value,
        top: 10,
      }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "启动扫描失败");
    }
    renderMarketScan(payload.snapshot);
    startMarketUpdates();
    setState("scan");
  } catch (error) {
    errorState.textContent = error.message;
    setState("error");
  } finally {
    scanButton.disabled = false;
  }
}

async function stopMarketScan() {
  await fetch("/api/market-scan/stop", { method: "POST" });
  stopMarketUpdates();
  document.querySelector("#scan-refresh-status").textContent = "已停止";
}

function startMarketUpdates() {
  if ("EventSource" in window) {
    startMarketStream();
    return;
  }
  refreshMarketScanStatus();
}

function stopMarketUpdates() {
  stopMarketStream();
}

function startMarketStream() {
  if (marketEventSource) {
    return;
  }
  marketEventSource = new EventSource("/api/market-scan/stream");
  marketEventSource.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.ok) {
      renderMarketScan(payload.snapshot);
    }
  };
  marketEventSource.onerror = () => {
    stopMarketStream();
    refreshMarketScanStatus();
  };
}

function stopMarketStream() {
  if (marketEventSource) {
    marketEventSource.close();
    marketEventSource = null;
  }
}

async function refreshMarketScanStatus() {
  const response = await fetch("/api/market-scan/status");
  const payload = await response.json();
  if (payload.ok) {
    renderMarketScan(payload.snapshot);
  }
}

function renderMarketScan(snapshot) {
  const results = (snapshot.top_results || [])
    .sort((left, right) => right.score - left.score || right.probability - left.probability)
    .slice(0, 10);
  document.querySelector("#scan-count").textContent = results.length;
  document.querySelector("#scan-fetched").textContent = snapshot.fetched_count || 0;
  document.querySelector("#scan-finished").textContent = snapshot.scanned_count;
  document.querySelector("#scan-total").textContent = snapshot.total_count || "--";
  document.querySelector("#scan-skipped").textContent = snapshot.skipped_count || 0;
  document.querySelector("#scan-failed").textContent = snapshot.failed_count || 0;
  document.querySelector("#scan-refresh-status").textContent = statusText(snapshot);
  document.querySelector("#scan-meta").textContent = `MARKET SCAN · ${snapshot.last_started_at || "--"}`;

  const tbody = document.querySelector("#scan-results");
  tbody.innerHTML = "";
  results.forEach((item, index) => {
    const row = document.createElement("tr");
    appendCell(row, String(index + 1));
    const stockCell = document.createElement("td");
    const symbolNode = document.createElement("strong");
    const nameNode = document.createElement("span");
    symbolNode.textContent = item.symbol;
    nameNode.textContent = item.name;
    stockCell.append(symbolNode, nameNode);
    row.appendChild(stockCell);
    appendCell(row, item.action);
    appendCell(row, String(item.score));
    appendCell(row, formatPercent(item.probability));
    appendCell(row, item.chan_summary || "--");
    appendCell(row, item.reason);
    appendCell(row, item.risk);
    tbody.appendChild(row);
  });
  const emptyNode = document.querySelector("#scan-empty");
  emptyNode.textContent = scanEmptyText(snapshot);
  emptyNode.classList.toggle("hidden", results.length !== 0);
}

function statusText(snapshot) {
  return snapshot.status_message || snapshot.status;
}

function scanEmptyText(snapshot) {
  if ((snapshot.failed_count || 0) > 0) {
    return "暂时没有可展示股票，已有股票抓取失败或数据不足，请查看刷新状态。";
  }
  if ((snapshot.skipped_count || 0) > 0) {
    return "暂时没有可展示股票，当前批次可能多为创业板或科创板，已按规则跳过。";
  }
  return "正在等待第一只股票完成分析。";
}

function appendCell(row, text) {
  const cell = document.createElement("td");
  cell.textContent = text;
  row.appendChild(cell);
}

function renderList(selector, items) {
  const target = document.querySelector(selector);
  target.innerHTML = "";
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    target.appendChild(li);
  });
}

function renderMl(prediction) {
  const target = document.querySelector("#ml-detail");
  target.innerHTML = "";
  const rows = [
    ["算法", prediction.algorithm_name],
    ["预测周期", prediction.horizon_days > 1 ? `未来 ${prediction.horizon_days} 个交易日` : "下一交易日"],
    ["历史达标占比", formatPercent(prediction.buy_probability)],
    ["训练样本数", String(prediction.sample_count)],
  ];
  if (prediction.neighbor_count > 0) {
    rows.push(["最近邻样本数", String(prediction.neighbor_count)]);
    rows.push(["最近邻达标样本数", String(prediction.positive_count)]);
  }
  rows.forEach(([label, value]) => {
    const row = document.createElement("div");
    const labelNode = document.createElement("span");
    const valueNode = document.createElement("strong");
    labelNode.textContent = label;
    valueNode.textContent = value;
    row.append(labelNode, valueNode);
    target.appendChild(row);
  });
}

function renderChan(chan) {
  if (!chan) return;
  document.querySelector("#chan-trend").textContent = chan.trend;
  document.querySelector("#chan-position").textContent = chan.position;
  document.querySelector("#chan-buy").textContent = chan.buy_signal;
  document.querySelector("#chan-risk").textContent = chan.risk_signal;
  const sign = chan.score_adjustment > 0 ? "+" : "";
  document.querySelector("#chan-adjustment").textContent = `结构调整 ${sign}${chan.score_adjustment}`;
  document.querySelector("#chan-recommendation").textContent = chan.recommendation;
  document.querySelector("#chan-explanation").textContent = chan.explanation;
}

function drawStockChart(chart, chan) {
  const canvas = document.querySelector("#stock-chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);

  const opens = chart.opens || chart.closes;
  const closes = chart.closes;
  const highs = chart.highs || closes;
  const lows = chart.lows || closes;
  const volumes = chart.volumes;
  const centerPrices = chan && chan.center ? [chan.center.lower, chan.center.upper] : [];
  const minPrice = Math.min(...lows, ...centerPrices);
  const maxPrice = Math.max(...highs, ...centerPrices);
  const maxVolume = Math.max(...volumes);
  const pad = 34;
  const chartHeight = height * 0.68;
  const volumeTop = chartHeight + 34;
  const volumeHeight = height - volumeTop - 20;

  drawGrid(ctx, width, height, pad);
  drawChanCenter(ctx, chan, closes.length, minPrice, maxPrice, width, chartHeight, pad);
  drawCandles(ctx, opens, highs, lows, closes, minPrice, maxPrice, width, chartHeight, pad);
  drawChanStrokes(ctx, chan, closes.length, minPrice, maxPrice, width, chartHeight, pad);
  drawChanPoints(ctx, chan, closes.length, minPrice, maxPrice, width, chartHeight, pad);

  const barWidth = (width - pad * 2) / volumes.length * 0.55;
  volumes.forEach((value, index) => {
    const x = pad + (index / (volumes.length - 1)) * (width - pad * 2) - barWidth / 2;
    const barHeight = normalize(value, 0, maxVolume) * volumeHeight;
    ctx.fillStyle = closes[index] >= opens[index] ? "rgba(88, 214, 166, 0.45)" : "rgba(255, 125, 125, 0.42)";
    ctx.fillRect(x, volumeTop + volumeHeight - barHeight, barWidth, barHeight);
  });

  ctx.fillStyle = "#8da6c9";
  ctx.font = "22px sans-serif";
  ctx.fillText(`收盘 ${formatNumber(closes[closes.length - 1])}`, pad, 28);
}

function drawCandles(ctx, opens, highs, lows, closes, minPrice, maxPrice, width, chartHeight, pad) {
  const candleWidth = Math.max(4, ((width - pad * 2) / closes.length) * 0.58);
  closes.forEach((close, index) => {
    const open = opens[index];
    const high = highs[index];
    const low = lows[index];
    const x = xForIndex(index, width, pad, closes.length);
    const openY = yForPrice(open, minPrice, maxPrice, chartHeight, pad);
    const closeY = yForPrice(close, minPrice, maxPrice, chartHeight, pad);
    const highY = yForPrice(high, minPrice, maxPrice, chartHeight, pad);
    const lowY = yForPrice(low, minPrice, maxPrice, chartHeight, pad);
    const rising = close >= open;
    ctx.strokeStyle = rising ? "#35d399" : "#ff7185";
    ctx.fillStyle = rising ? "rgba(53, 211, 153, 0.24)" : "rgba(255, 113, 133, 0.68)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x, highY);
    ctx.lineTo(x, lowY);
    ctx.stroke();
    const bodyTop = Math.min(openY, closeY);
    const bodyHeight = Math.max(2, Math.abs(closeY - openY));
    ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
    ctx.strokeRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
  });
}

function drawChanCenter(ctx, chan, pointCount, minClose, maxClose, width, chartHeight, pad) {
  if (!chan || !chan.center) return;
  const center = chan.center;
  const startX = xForIndex(center.start_index, width, pad, pointCount);
  const endX = xForIndex(center.end_index, width, pad, pointCount);
  const upperY = yForPrice(center.upper, minClose, maxClose, chartHeight, pad);
  const lowerY = yForPrice(center.lower, minClose, maxClose, chartHeight, pad);
  ctx.fillStyle = "rgba(119, 213, 255, 0.12)";
  ctx.fillRect(startX, upperY, Math.max(8, endX - startX), lowerY - upperY);
  ctx.strokeStyle = "rgba(119, 213, 255, 0.64)";
  ctx.lineWidth = 1.5;
  ctx.strokeRect(startX, upperY, Math.max(8, endX - startX), lowerY - upperY);
}

function drawChanStrokes(ctx, chan, pointCount, minClose, maxClose, width, chartHeight, pad) {
  if (!chan || !chan.strokes) return;
  ctx.lineWidth = 2;
  chan.strokes.forEach((stroke) => {
    const startX = xForIndex(stroke.start_index, width, pad, pointCount);
    const endX = xForIndex(stroke.end_index, width, pad, pointCount);
    const startY = yForPrice(stroke.start_price, minClose, maxClose, chartHeight, pad);
    const endY = yForPrice(stroke.end_price, minClose, maxClose, chartHeight, pad);
    ctx.strokeStyle = stroke.direction === "up" ? "rgba(88, 214, 166, 0.92)" : "rgba(255, 125, 125, 0.88)";
    ctx.beginPath();
    ctx.moveTo(startX, startY);
    ctx.lineTo(endX, endY);
    ctx.stroke();
  });
}

function drawChanPoints(ctx, chan, pointCount, minClose, maxClose, width, chartHeight, pad) {
  if (!chan || !chan.points) return;
  chan.points.forEach((point) => {
    const x = xForIndex(point.index, width, pad, pointCount);
    const y = yForPrice(point.price, minClose, maxClose, chartHeight, pad);
    ctx.fillStyle = point.kind === "top" ? "#ff7185" : "#77d5ff";
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "rgba(237, 243, 239, 0.82)";
    ctx.font = "14px sans-serif";
    ctx.fillText(point.kind === "top" ? "顶" : "底", x + 7, y - 7);
  });
}

function xForIndex(index, width, pad, pointCount) {
  const count = pointCount || 90;
  if (count <= 1) return pad;
  return pad + (index / (count - 1)) * (width - pad * 2);
}

function yForPrice(price, minClose, maxClose, chartHeight, pad) {
  return pad + (1 - normalize(price, minClose, maxClose)) * (chartHeight - pad);
}

function drawGrid(ctx, width, height, pad) {
  ctx.strokeStyle = "rgba(151, 190, 255, 0.12)";
  ctx.lineWidth = 1;
  for (let index = 0; index < 5; index += 1) {
    const y = pad + index * ((height - pad * 2) / 4);
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(width - pad, y);
    ctx.stroke();
  }
}

function normalize(value, min, max) {
  if (max === min) return 0.5;
  return (value - min) / (max - min);
}

function formatNumber(value) {
  return Number(value).toFixed(2);
}

function formatPercent(value) {
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function formatAmount(value) {
  const number = Number(value);
  if (Math.abs(number) >= 100000000) return `${(number / 100000000).toFixed(2)}亿`;
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)}万`;
  return number.toFixed(2);
}

function parseOptionalNumber(value) {
  const cleaned = String(value || "").trim();
  if (!cleaned) return null;
  const number = Number(cleaned);
  return Number.isFinite(number) ? number : null;
}

function setupPointerEffects() {
  window.addEventListener("pointermove", (event) => {
    const x = `${(event.clientX / window.innerWidth) * 100}%`;
    const y = `${(event.clientY / window.innerHeight) * 100}%`;
    document.documentElement.style.setProperty("--pointer-x", x);
    document.documentElement.style.setProperty("--pointer-y", y);
  });

  document.querySelectorAll("button, .score-card, .detail-card, .chart-card, .chan-panel").forEach((node) => {
    node.addEventListener("pointermove", (event) => {
      const rect = node.getBoundingClientRect();
      const offsetX = (event.clientX - rect.left - rect.width / 2) / rect.width;
      const offsetY = (event.clientY - rect.top - rect.height / 2) / rect.height;
      node.style.transform = `translate3d(${offsetX * 5}px, ${offsetY * 5 - 2}px, 0)`;
    });
    node.addEventListener("pointerleave", () => {
      node.style.transform = "";
    });
  });
}

setupPointerEffects();
