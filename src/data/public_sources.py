from __future__ import annotations

import json
import os
import threading
import time
from hashlib import sha256
from pathlib import Path
from datetime import datetime, time as datetime_time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from data.errors import DataRefreshError
from data.models import PriceVolumeBar, StockInfo

_STOCK_NAME_CACHE: dict[str, str] | None = None
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
DEFAULT_KLINE_DAYS = 260
SHANGHAI_INDEX_SYMBOLS = {"000016", "000300", "000852", "000905"}
SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
MARKET_CLOSE_TIME = datetime_time(15, 0)


def fetch_public_stock_bars(
    symbol: str,
    name: str | None = None,
    adjustment_type: str = "qfq",
) -> list[PriceVolumeBar]:
    if adjustment_type not in {"qfq", "none"}:
        raise ValueError("复权类型只支持 qfq 或 none。")
    errors = []
    for source in _kline_source_order(adjustment_type):
        try:
            if source == "sina":
                bars = _fetch_sina_stock_bars(symbol, name)
            elif source == "tencent":
                bars = _fetch_tencent_stock_bars(symbol, name, adjustment_type)
            else:
                continue
            bars = _exclude_incomplete_current_bar(bars)
            if not bars:
                raise DataRefreshError(f"{source} 未返回已完成的日线数据")
            return bars
        except DataRefreshError as exc:
            errors.append(f"{source}：{exc}")
    raise DataRefreshError("；".join(errors) or f"未能获取 {symbol} 的有效日线数据")


def _exclude_incomplete_current_bar(
    bars: list[PriceVolumeBar],
    now: datetime | None = None,
) -> list[PriceVolumeBar]:
    current = now or datetime.now(SHANGHAI_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TIMEZONE)
    else:
        current = current.astimezone(SHANGHAI_TIMEZONE)
    if current.time() >= MARKET_CLOSE_TIME:
        return bars
    # 仅剔除上海时区当天的动态 K 线，周末和历史日期不会被误删
    return [bar for bar in bars if bar.trade_date != current.date()]


def _fetch_sina_stock_bars(symbol: str, stock_name: str | None) -> list[PriceVolumeBar]:
    market_symbol = _to_market_symbol(symbol)
    url = _sina_kline_url(market_symbol)
    payload = _fetch_json(url, "https://finance.sina.com.cn/")
    if not isinstance(payload, list):
        raise DataRefreshError(f"新浪未返回 {symbol} 的有效日线数据")

    bars = []
    for item in payload:
        try:
            close = float(item["close"])
            volume = float(item["volume"])
            bars.append(
                PriceVolumeBar(
                    name=stock_name or "未知",
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


def _fetch_tencent_stock_bars(
    symbol: str,
    stock_name: str | None,
    adjustment_type: str = "qfq",
) -> list[PriceVolumeBar]:
    market_symbol = _to_market_symbol(symbol)
    payload = _fetch_json(
        _tencent_kline_url(market_symbol, adjustment_type),
        "https://gu.qq.com/",
    )
    data = payload.get("data", {}).get(market_symbol) if isinstance(payload, dict) else None
    if isinstance(data, dict):
        rows = (
            data.get("qfqday")
            if adjustment_type == "qfq"
            else data.get("day")
        )
    else:
        rows = None
    if not rows:
        raise DataRefreshError(f"腾讯未返回 {symbol} 的有效日线数据")

    resolved_name = stock_name or _tencent_stock_name(data, market_symbol)
    bars = []
    for row in rows:
        try:
            close = float(row[2])
            volume = float(row[5])
            bars.append(
                PriceVolumeBar(
                    name=resolved_name,
                    trade_date=datetime.strptime(row[0], "%Y-%m-%d").date(),
                    open=float(row[1]),
                    high=float(row[3]),
                    low=float(row[4]),
                    close=close,
                    volume=volume,
                    amount=close * volume,
                )
            )
        except (IndexError, TypeError, ValueError) as exc:
            raise DataRefreshError(f"腾讯日线数据格式不符合预期：{exc}") from exc
    if not bars:
        raise DataRefreshError(f"腾讯未返回 {symbol} 的有效日线数据")
    return bars


def _tencent_stock_name(data: dict, market_symbol: str) -> str:
    quote = data.get("qt", {}).get(market_symbol) if isinstance(data.get("qt"), dict) else None
    if isinstance(quote, list) and len(quote) > 1:
        return str(quote[1] or "未知")
    return "未知"


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
    last_error = None
    for attempt in range(_request_retries() + 1):
        request = Request(url, headers=headers)
        _wait_for_request_slot()
        try:
            with urlopen(request, timeout=_request_timeout()) as response:
                text = response.read().decode("utf-8", errors="replace")
                _persist_raw_response(url, text)
                break
        except OSError as exc:
            last_error = exc
            if attempt >= _request_retries():
                raise DataRefreshError(f"公开行情接口请求失败：{exc}") from exc
            time.sleep(_retry_delay_seconds(attempt))
    else:
        raise DataRefreshError(f"公开行情接口请求失败：{last_error}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataRefreshError("公开行情接口响应不是有效 JSON") from exc


def _persist_raw_response(url: str, text: str) -> None:
    raw_root = Path(os.getenv("PUBLIC_DATA_RAW_DIR", "data/raw/http"))
    source = _source_name(url)
    fetched_at = datetime.now().astimezone()
    content_hash = sha256(text.encode("utf-8")).hexdigest()
    target_dir = raw_root / source / fetched_at.strftime("%Y%m%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{fetched_at.strftime('%H%M%S_%f')}_{content_hash[:16]}.json"
    metadata = {
        "schema_version": 1,
        "source": source,
        "url": url,
        "fetched_at": fetched_at.isoformat(),
        "content_sha256": content_hash,
        "body": text,
    }
    temporary_path = target_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    temporary_path.replace(target_path)


def _source_name(url: str) -> str:
    if "sina.com.cn" in url:
        return "sina"
    if "gtimg.cn" in url or "qq.com" in url:
        return "tencent"
    return "public"


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


def _tencent_kline_url(
    market_symbol: str,
    adjustment_type: str = "qfq",
) -> str:
    days = int(os.getenv("PUBLIC_KLINE_DAYS", str(DEFAULT_KLINE_DAYS)))
    suffix = ",qfq" if adjustment_type == "qfq" else ""
    query = urlencode({"param": f"{market_symbol},day,,,{days}{suffix}"})
    return f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?{query}"


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


def _to_market_symbol(symbol: str) -> str:
    # 常用宽基指数使用上海市场前缀，避免把 000300 等指数误作深圳股票。
    if symbol in SHANGHAI_INDEX_SYMBOLS:
        return f"sh{symbol}"
    if symbol.startswith(("5", "6", "9")):
        return f"sh{symbol}"
    return f"sz{symbol}"


def _kline_source_order(adjustment_type: str = "qfq") -> list[str]:
    # 新浪日线不提供与腾讯一致的前复权口径，模型序列禁止跨口径兜底。
    if adjustment_type == "qfq":
        return ["tencent"]
    source = os.getenv("PUBLIC_KLINE_SOURCE", "tencent").strip().lower()
    if source == "sina":
        return ["sina", "tencent"]
    return ["tencent", "sina"]


def _request_timeout() -> float:
    return max(1, float(os.getenv("PUBLIC_DATA_TIMEOUT_SECONDS", "8")))


def _request_retries() -> int:
    return max(0, int(os.getenv("PUBLIC_DATA_RETRIES", "2")))


def _retry_delay_seconds(attempt: int) -> float:
    return min(8.0, float(os.getenv("PUBLIC_DATA_RETRY_BASE_SECONDS", "1")) * (attempt + 1))


def _wait_for_request_slot() -> None:
    global _LAST_REQUEST_AT
    interval = max(0.0, float(os.getenv("PUBLIC_REQUEST_INTERVAL_SECONDS", "0.25")))
    if interval <= 0:
        return
    with _REQUEST_LOCK:
        now = time.time()
        wait_seconds = interval - (now - _LAST_REQUEST_AT)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _LAST_REQUEST_AT = time.time()
