from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from data.errors import DataRefreshError
from data.models import PriceVolumeBar, StockInfo

_STOCK_NAME_CACHE: dict[str, str] | None = None
DEFAULT_KLINE_DAYS = 250


def fetch_public_stock_bars(symbol: str, name: str | None = None) -> list[PriceVolumeBar]:
    market_symbol = _to_sina_symbol(symbol)
    url = _sina_kline_url(market_symbol)
    payload = _fetch_json(url, "https://finance.sina.com.cn/")
    if not isinstance(payload, list):
        raise DataRefreshError(f"新浪未返回 {symbol} 的有效日线数据")

    stock_name = name or find_public_stock_name(symbol)
    bars = []
    for item in payload:
        try:
            close = float(item["close"])
            volume = float(item["volume"])
            bars.append(
                PriceVolumeBar(
                    name=stock_name,
                    trade_date=datetime.strptime(item["day"], "%Y-%m-%d").date(),
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=close,
                    volume=volume,
                    amount=close * volume,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DataRefreshError(f"新浪日线数据格式不符合预期：{exc}") from exc

    if not bars:
        raise DataRefreshError(f"新浪未返回 {symbol} 的有效日线数据")
    return bars


def fetch_public_a_stock_symbols() -> list[StockInfo]:
    return list(iter_public_a_stock_symbols())


def iter_public_a_stock_symbols():
    page_size = max(20, int(os.getenv("PUBLIC_STOCK_LIST_PAGE_SIZE", "80")))
    page = 1
    total = _fetch_sina_stock_count()
    seen = set()
    while (page - 1) * page_size < total:
        rows = _fetch_json(_sina_stock_list_url(page, page_size), "https://finance.sina.com.cn/")
        if not rows:
            break
        for row in rows:
            symbol = str(row.get("code") or "").strip()
            name = str(row.get("name") or "").strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            yield StockInfo(symbol=symbol, name=name or "未知")
        page += 1


def find_public_stock_name(symbol: str) -> str:
    names = _stock_name_map()
    return names.get(symbol, "未知")


def _stock_name_map() -> dict[str, str]:
    global _STOCK_NAME_CACHE
    if _STOCK_NAME_CACHE is None:
        _STOCK_NAME_CACHE = {stock.symbol: stock.name for stock in iter_public_a_stock_symbols()}
    return _STOCK_NAME_CACHE


def _fetch_json(url: str, referer: str):
    headers = {
        "User-Agent": os.getenv("PUBLIC_DATA_USER_AGENT", "Mozilla/5.0"),
        "Accept": "application/json,text/plain,*/*",
        "Referer": referer,
    }
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=_request_timeout()) as response:
            text = response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise DataRefreshError(f"公开行情接口请求失败：{exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataRefreshError("公开行情接口响应不是有效 JSON") from exc


def _sina_kline_url(market_symbol: str) -> str:
    query = urlencode(
        {
            "symbol": market_symbol,
            "scale": 240,
            "ma": "no",
            "datalen": int(os.getenv("PUBLIC_KLINE_DAYS", str(DEFAULT_KLINE_DAYS))),
        }
    )
    return f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?{query}"


def _fetch_sina_stock_count() -> int:
    payload = _fetch_json(_sina_stock_count_url(), "https://finance.sina.com.cn/")
    try:
        return int(payload)
    except (TypeError, ValueError) as exc:
        raise DataRefreshError("新浪未返回全 A 股票数量") from exc


def _sina_stock_count_url() -> str:
    return "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount?node=hs_a"


def _sina_stock_list_url(page: int, page_size: int) -> str:
    query = urlencode(
        {
            "page": page,
            "num": page_size,
            "sort": "symbol",
            "asc": 1,
            "node": "hs_a",
            "symbol": "",
            "_s_r_a": "init",
        }
    )
    return f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?{query}"


def _to_sina_symbol(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return f"sh{symbol}"
    return f"sz{symbol}"


def _request_timeout() -> float:
    return max(1, float(os.getenv("PUBLIC_DATA_TIMEOUT_SECONDS", "8")))
