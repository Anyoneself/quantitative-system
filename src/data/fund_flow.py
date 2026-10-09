from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from data.errors import DataRefreshError
from data.models import FundFlowBar


_FUND_FLOW_CACHE: dict[tuple, tuple[float, list[FundFlowBar] | None]] = {}
_CACHE_LOCK = threading.Lock()
_CONSECUTIVE_FAILURES = 0
_BLOCKED_UNTIL = 0.0


def fetch_public_fund_flow_bars(symbol: str, limit: int = 20) -> list[FundFlowBar]:
    global _CONSECUTIVE_FAILURES, _BLOCKED_UNTIL
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    cache_key = (symbol, limit, now.date(), now.hour >= 15)
    with _CACHE_LOCK:
        cached = _FUND_FLOW_CACHE.get(cache_key)
        if cached and 0 <= time.monotonic() - cached[0] < (900 if cached[1] else 60):
            if cached[1] is None:
                raise DataRefreshError("该股资金流暂不可用，稍后重试")
            return cached[1]
        if time.monotonic() < _BLOCKED_UNTIL:
            raise DataRefreshError("资金流服务连续失败，暂时跳过此可选数据")
    try:
        bars = _load_fund_flow_bars(symbol, limit)
    except DataRefreshError:
        with _CACHE_LOCK:
            _CONSECUTIVE_FAILURES += 1
            if _CONSECUTIVE_FAILURES >= 3:
                _BLOCKED_UNTIL = time.monotonic() + 60
            if len(_FUND_FLOW_CACHE) >= 6000:
                _FUND_FLOW_CACHE.pop(next(iter(_FUND_FLOW_CACHE)))
            _FUND_FLOW_CACHE[cache_key] = (time.monotonic(), None)
        raise
    with _CACHE_LOCK:
        _CONSECUTIVE_FAILURES = 0
        _BLOCKED_UNTIL = 0.0
        if len(_FUND_FLOW_CACHE) >= 6000:
            _FUND_FLOW_CACHE.pop(next(iter(_FUND_FLOW_CACHE)))
        _FUND_FLOW_CACHE[cache_key] = (time.monotonic(), bars)
    return bars


def _load_fund_flow_bars(symbol: str, limit: int) -> list[FundFlowBar]:
    url = _eastmoney_fund_flow_url(symbol, limit)
    payload = _fetch_json(url, "https://quote.eastmoney.com/")
    rows = payload.get("data", {}).get("klines") if isinstance(payload, dict) else None
    if not rows:
        raise DataRefreshError(f"东方财富未返回 {symbol} 的资金流数据")

    bars = []
    for row in rows:
        try:
            parts = str(row).split(",")
            bars.append(
                FundFlowBar(
                    trade_date=datetime.strptime(parts[0], "%Y-%m-%d").date(),
                    main_net_inflow=float(parts[1]),
                    super_large_net_inflow=float(parts[2]),
                    large_net_inflow=float(parts[3]),
                    medium_net_inflow=float(parts[4]),
                    small_net_inflow=float(parts[5]),
                    main_net_inflow_ratio=float(parts[6]) / 100,
                )
            )
        except (IndexError, TypeError, ValueError) as exc:
            raise DataRefreshError(f"东方财富资金流数据格式不符合预期：{exc}") from exc
    return bars


def _eastmoney_fund_flow_url(symbol: str, limit: int) -> str:
    query = urlencode(
        {
            "secid": f"{_market_id(symbol)}.{symbol}",
            "lmt": limit,
            "klt": 101,
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
        }
    )
    return f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?{query}"


def _market_id(symbol: str) -> int:
    return 1 if symbol.startswith("6") else 0


def _fetch_json(url: str, referer: str):
    headers = {
        "User-Agent": os.getenv("PUBLIC_DATA_USER_AGENT", "Mozilla/5.0"),
        "Accept": "application/json,text/plain,*/*",
        "Referer": referer,
    }
    request = Request(url, headers=headers)
    try:
        timeout = max(.5, min(float(os.getenv("FUND_FLOW_TIMEOUT_SECONDS", "2.5")),
                             float(os.getenv("PUBLIC_DATA_TIMEOUT_SECONDS", "8"))))
        with urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise DataRefreshError(f"资金流接口请求失败：{exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataRefreshError("资金流接口响应不是有效 JSON") from exc
