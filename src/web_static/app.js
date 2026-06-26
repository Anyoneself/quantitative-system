const form = document.querySelector("#advisor-form");
const scanForm = document.querySelector("#scan-form");
const symbolInput = document.querySelector("#symbol");
const algorithmInput = document.querySelector("#algorithm");
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

  document.querySelector("#stock-meta").textContent =
    `${indicators.symbol} · ${indicators.name || "未知"} · ${indicators.analysis_date}`;
  document.querySelector("#headline").textContent =
    `${actionText[advice.action] || advice.action}：${beginner.headline}`;
  document.querySelector("#score").textContent = advice.score;
  document.querySelector("#close-price").textContent = formatNumber(indicators.close);
  document.querySelector("#return-1d").textContent = formatPercent(indicators.return_1d);
  document.querySelector("#return-5d").textContent = formatPercent(indicators.return_5d);
  document.querySelector("#volume-ratio").textContent = indicators.volume_ratio_20d.toFixed(2);
  document.querySelector("#data-range").textContent =
    `${indicators.data_end_date} · ${indicators.sample_days} 个交易日`;

  renderList("#reasons", advice.reasons);
  renderList("#evidence", advice.evidence || []);
  renderList("#risks", advice.risks);
  renderList("#observations", advice.observations);
  renderList("#beginner", [...beginner.plain_language, ...beginner.next_steps]);
  renderMl(prediction);
  drawStockChart(payload.chart);
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
    ["历史相似上涨占比", formatPercent(prediction.buy_probability)],
    ["训练样本数", String(prediction.sample_count)],
  ];
  if (prediction.neighbor_count > 0) {
    rows.push(["最近邻样本数", String(prediction.neighbor_count)]);
    rows.push(["最近邻上涨样本数", String(prediction.positive_count)]);
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

function drawStockChart(chart) {
  const canvas = document.querySelector("#stock-chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);

  const closes = chart.closes;
  const volumes = chart.volumes;
  const minClose = Math.min(...closes);
  const maxClose = Math.max(...closes);
  const maxVolume = Math.max(...volumes);
  const pad = 34;
  const chartHeight = height * 0.68;
  const volumeTop = chartHeight + 34;
  const volumeHeight = height - volumeTop - 20;

  drawGrid(ctx, width, height, pad);

  ctx.lineWidth = 3;
  ctx.strokeStyle = "#58d6a6";
  ctx.beginPath();
  closes.forEach((value, index) => {
    const x = pad + (index / (closes.length - 1)) * (width - pad * 2);
    const y = pad + (1 - normalize(value, minClose, maxClose)) * (chartHeight - pad);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  const barWidth = (width - pad * 2) / volumes.length * 0.55;
  volumes.forEach((value, index) => {
    const x = pad + (index / (volumes.length - 1)) * (width - pad * 2) - barWidth / 2;
    const barHeight = normalize(value, 0, maxVolume) * volumeHeight;
    ctx.fillStyle = "rgba(217, 182, 106, 0.55)";
    ctx.fillRect(x, volumeTop + volumeHeight - barHeight, barWidth, barHeight);
  });

  ctx.fillStyle = "#9ba9a3";
  ctx.font = "22px sans-serif";
  ctx.fillText(`收盘 ${formatNumber(closes[closes.length - 1])}`, pad, 28);
}

function drawGrid(ctx, width, height, pad) {
  ctx.strokeStyle = "rgba(211, 223, 215, 0.10)";
  ctx.lineWidth = 1;
  for (let index = 0; index < 5; index += 1) {
    const y = pad + index * ((height - pad * 2) / 4);
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(width - pad, y);
    ctx.stroke();
  }
}

function drawAmbientChart() {
  const canvas = document.querySelector("#ambient-chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "rgba(88, 214, 166, 0.62)";
  ctx.lineWidth = 4;
  ctx.beginPath();
  for (let index = 0; index < 72; index += 1) {
    const x = (index / 71) * width;
    const y = height * 0.54 + Math.sin(index * 0.38) * 34 - index * 0.45;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
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

drawAmbientChart();
