from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from data.errors import DataRefreshError
from data.models import FundFlowBar


_FUND_FLOW_CACHE: dict[tuple[str, int], list[FundFlowBar]] = {}


def fetch_public_fund_flow_bars(symbol: str, limit: int = 20) -> list[FundFlowBar]:
    cache_key = (symbol, limit)
    if cache_key in _FUND_FLOW_CACHE:
        return _FUND_FLOW_CACHE[cache_key]

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
    _FUND_FLOW_CACHE[cache_key] = bars
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
        with urlopen(request, timeout=float(os.getenv("PUBLIC_DATA_TIMEOUT_SECONDS", "8"))) as response:
            text = response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise DataRefreshError(f"资金流接口请求失败：{exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataRefreshError("资金流接口响应不是有效 JSON") from exc