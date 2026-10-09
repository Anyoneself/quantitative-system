from __future__ import annotations

import json
import math
import os
import threading
import time
from hashlib import sha256
from functools import lru_cache
from pathlib import Path
from datetime import date, datetime, time as datetime_time
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from typing import Callable

from data.errors import DataRefreshError, DataSourceUnavailable
from data.models import PriceVolumeBar, StockInfo
from data.quality import validate_price_bars

_STOCK_NAME_CACHE: dict[str, str] | None = None
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_SCAN_SOURCE_LOCK = threading.Lock()
_SCAN_SOURCE_RETRY_AT = {}
_ARCHIVE_LOCK = threading.Lock()
DEFAULT_KLINE_DAYS = 260
SHANGHAI_INDEX_SYMBOLS = {"000016", "000300", "000852", "000905"}
SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
MARKET_CLOSE_TIME = datetime_time(15, 0)

def fetch_public_market_stock_bars(symbol: str, name: str | None = None):
    """Public-universe scans only: account-authorized lookups keep their original Tencent-only path."""
    errors = []
    stock_data_error = False
    for source, loader, label in (
        ("tencent", _fetch_tencent_stock_bars, "腾讯前复权"),
        ("eastmoney", _fetch_eastmoney_stock_bars, "东方财富前复权"),
    ):
        with _SCAN_SOURCE_LOCK:
            retry_at = _SCAN_SOURCE_RETRY_AT.get(source, 0)
        if time.monotonic() < retry_at:
            errors.append(f"{label}暂不可用，等待重试")
            continue
        try:
            bars = validate_price_bars(_exclude_incomplete_current_bar(loader(symbol, name)))
            return bars, label
        except DataRefreshError as exc:
            errors.append(f"{label}：{exc}")
            if isinstance(exc, DataSourceUnavailable):
                with _SCAN_SOURCE_LOCK:
                    _SCAN_SOURCE_RETRY_AT[source] = time.monotonic() + 60
            else:
                stock_data_error = True
    cached = _cached_tencent_market_bars(symbol, name)
    if cached is not None:
        return cached
    error_type = DataRefreshError if stock_data_error else DataSourceUnavailable
    raise error_type("；".join(errors))


def _fetch_eastmoney_stock_bars(symbol, name):
    query = urlencode({
        "secid": f"{1 if symbol.startswith('6') else 0}.{symbol}", "klt": 101, "fqt": 1,
        "lmt": int(os.getenv("PUBLIC_KLINE_DAYS", str(DEFAULT_KLINE_DAYS))), "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    })
    payload = _fetch_json(f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{query}",
                          "https://quote.eastmoney.com/", timeout=5, retries=0)
    try:
        data = payload["data"]
        if str(data["code"]) != symbol:
            raise ValueError("stock code mismatch")
        bars = []
        for row in data["klines"]:
            fields = row.split(",")
            bars.append(PriceVolumeBar(name or data["name"], datetime.strptime(fields[0], "%Y-%m-%d").date(),
                                       float(fields[1]), float(fields[3]), float(fields[4]), float(fields[2]),
                                       float(fields[5]) * 100, float(fields[6])))
        if not bars:
            raise ValueError("empty bars")
        return bars
    except (KeyError, IndexError, ValueError, TypeError, AttributeError) as exc:
        raise DataRefreshError("东方财富前复权日线不可用或格式异常") from exc


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


def _fetch_sina_stock_bars(symbol: str, stock_name: str | None, days: int | None = None) -> list[PriceVolumeBar]:
    market_symbol = _to_market_symbol(symbol)
    url = _sina_kline_url(market_symbol, days)
    payload = _fetch_json(url, "https://finance.sina.com.cn/")
    if not isinstance(payload, list):
        raise DataRefreshError(f"新浪未返回 {symbol} 的有效日线数据")

    bars = []
    for item in payload:
        try:
            close = float(item["close"])
            # 新浪日线 volume 为股；接口不含成交额，close * volume 仅为估算值。
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
    return _parse_tencent_stock_bars(payload, symbol, stock_name, adjustment_type)


def _parse_tencent_stock_bars(payload, symbol, stock_name, adjustment_type="qfq"):
    market_symbol = _to_market_symbol(symbol)
    data = payload.get("data", {}).get(market_symbol) if isinstance(payload, dict) else None
    if isinstance(data, dict):
        rows = (
            data.get("qfqday")
            if adjustment_type == "qfq"
            else data.get("day")
        )
        # 已知价格指数只返回 day；个股仍不得用未复权日线替代前复权序列。
        if not rows and symbol in SHANGHAI_INDEX_SYMBOLS:
            rows = data.get("day")
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


@lru_cache(maxsize=2)
def _tencent_archive_index(directory: str) -> dict[str, list[Path]]:
    # Index once per archive directory; never reread the entire archive for every scanned stock.
    index = {}
    for path in sorted(Path(directory).glob("*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("source") != "tencent" or not isinstance(record.get("url"), str):
                continue
            paths = index.setdefault(record["url"], [])
            if len(paths) < 3:
                paths.append(path)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return index


def _cached_tencent_market_bars(symbol: str, name: str | None):
    now = datetime.now(SHANGHAI_TIMEZONE)
    root = Path(os.getenv("PUBLIC_DATA_RAW_DIR", "data/raw/http"))
    directory = root / "tencent" / now.strftime("%Y%m%d")
    url = _tencent_kline_url(_to_market_symbol(symbol))
    with _ARCHIVE_LOCK:
        paths = _tencent_archive_index(str(directory.resolve())).get(url, [])
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(record["fetched_at"])
            if fetched.tzinfo is None:
                continue
            fetched = fetched.astimezone(SHANGHAI_TIMEZONE)
            if (record["source"] != "tencent" or record["url"] != url or fetched > now
                    or fetched.date() != now.date()
                    or (fetched.time() >= MARKET_CLOSE_TIME) != (now.time() >= MARKET_CLOSE_TIME)
                    or sha256(record["body"].encode("utf-8")).hexdigest() != record["content_sha256"]):
                continue
            bars = validate_price_bars(_exclude_incomplete_current_bar(
                _parse_tencent_stock_bars(json.loads(record["body"]), symbol, name), now,
            ))
            if not 0 <= (now.date() - bars[-1].trade_date).days <= 7:
                continue
            return bars, f"腾讯前复权 · 本地缓存（采集 {fetched:%m-%d %H:%M}）"
        except (OSError, ValueError, KeyError, TypeError, AttributeError, DataRefreshError):
            continue
    return None


def _tencent_stock_name(data: dict, market_symbol: str) -> str:
    quote = data.get("qt", {}).get(market_symbol) if isinstance(data.get("qt"), dict) else None
    if isinstance(quote, list) and len(quote) > 1:
        return str(quote[1] or "未知")
    return "未知"


def fetch_public_a_stock_symbols() -> list[StockInfo]:
    return list(iter_public_a_stock_symbols())


def fetch_public_a_stock_gainers(
    limit: int = 500, *, include_symbol: Callable[[str], bool] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[StockInfo]:
    return _fetch_public_a_stock_rank(limit, ascending=False, include_symbol=include_symbol, should_stop=should_stop)


def fetch_public_a_stock_losers(
    limit: int = 500, *, include_symbol: Callable[[str], bool] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[StockInfo]:
    return _fetch_public_a_stock_rank(limit, ascending=True, include_symbol=include_symbol, should_stop=should_stop)


def _fetch_public_a_stock_rank(
    limit: int, *, ascending: bool, include_symbol: Callable[[str], bool] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[StockInfo]:
    """Return the inspected rank prefix, retaining excluded rows for counts and source ranks."""
    label = "跌幅榜" if ascending else "涨幅榜"
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ValueError("涨跌幅股票池数量必须在 1 到 500 之间")
    stocks = []
    seen = set()
    eligible_count = 0
    total = _fetch_sina_stock_count() if include_symbol is not None else limit
    if type(total) is not int or total <= 0:
        raise DataRefreshError(f"{label}股票总数无效，未启动分析")
    previous_change = -math.inf if ascending else math.inf
    for page in range(1, (total + 99) // 100 + 1):
        if should_stop and should_stop():
            return stocks
        rows = _fetch_json(
            _sina_stock_list_url(page, 100, sort="changepercent", ascending=ascending),
            "https://finance.sina.com.cn/", timeout=5, retries=0,
        )
        remaining = min(100, total - (page - 1) * 100)
        if (not isinstance(rows, list) or not remaining <= len(rows) <= 100
                or include_symbol is not None and len(rows) != remaining):
            raise DataRefreshError(f"{label}第 {page} 页缺失或不完整，未形成目标 {limit} 只股票池，请重试")
        for row in rows[:remaining]:
            try:
                symbol = row["code"].strip()
                name = row["name"].strip()
                change = float(row["changepercent"])
                if (len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit()
                        or not name or not math.isfinite(change) or change < -100):
                    raise ValueError("invalid stock")
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise DataRefreshError(f"{label}证券代码、名称或涨幅无效，未启动分析") from exc
            out_of_order = change < previous_change if ascending else change > previous_change
            if symbol in seen or out_of_order:
                raise DataRefreshError(f"{label}翻页出现重复或排序变化，未启动分析，请重新读取")
            previous_change = change
            seen.add(symbol)
            stocks.append(StockInfo(symbol, name))
            eligible_count += int(include_symbol is None or include_symbol(symbol))
            if eligible_count == limit:
                return stocks
    return stocks


def fetch_public_a_stock_limit_pool(
    direction: str, limit: int = 100, *, trade_date: date | None = None,
    include_symbol: Callable[[str], bool] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[StockInfo]:
    """Read the ranked prefix until limit eligible members or the entire dedicated pool is exhausted."""
    if direction not in {"up", "down"}:
        raise ValueError("涨跌停方向不支持")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("涨跌停股票池数量必须在 1 到 100 之间")
    requested_date = trade_date or datetime.now(SHANGHAI_TIMEZONE).date()
    label = "涨停" if direction == "up" else "跌停"
    endpoint = "getTopicZTPool" if direction == "up" else "getTopicDTPool"
    # These are the public page's parameters, not an account credential.
    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
        "Pageindex": 0, "pagesize": limit, "sort": "amount:desc",
        "date": requested_date.strftime("%Y%m%d"),
    }
    stocks, seen, previous_amount = [], set(), math.inf
    eligible_count, page, total = 0, 0, None
    try:
        while True:
            if should_stop and should_stop():
                return stocks
            query = urlencode({**params, "Pageindex": page})
            payload = _fetch_json(f"https://push2ex.eastmoney.com/{endpoint}?{query}",
                                  "https://quote.eastmoney.com/ztb/detail", timeout=5, retries=0)
            if payload["rc"] != 0 or not isinstance(payload["data"], dict):
                raise ValueError("pool unavailable")
            data = payload["data"]
            if str(data["qdate"]) != requested_date.strftime("%Y%m%d"):
                raise DataRefreshError(f"{label}池日期不匹配，未使用旧股票池，请重试")
            reported_total, rows = data["tc"], data["pool"]
            if (type(reported_total) is not int or reported_total < 0
                    or total is not None and total != reported_total):
                raise ValueError("pool count changed")
            total = reported_total
            if not isinstance(rows, list) or len(rows) != min(max(0, total - page * limit), limit):
                raise ValueError("incomplete pool")
            for row in rows:
                symbol, name = row["c"].strip(), row["n"].strip()
                change, price, amount = float(row["zdp"]), float(row["p"]), float(row["amount"])
                if (len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit() or not name
                        or symbol in seen or not all(math.isfinite(v) for v in (change, price, amount))
                        or price <= 0 or not 0 <= amount <= previous_amount or change < -100
                        or (change <= 0 if direction == "up" else change >= 0)):
                    raise ValueError("invalid pool member")
                seen.add(symbol)
                previous_amount = amount
                stocks.append(StockInfo(symbol, name))
                eligible_count += int(include_symbol is None or include_symbol(symbol))
                if eligible_count == limit:
                    return stocks
            page += 1
            if page * limit >= total:
                return stocks
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise DataRefreshError(
            f"{requested_date.isoformat()} {label}池未发布或响应不完整，未启动分析；不会替换为普通涨跌幅榜"
        ) from exc


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


def _fetch_json(url: str, referer: str, **options):
    text = _fetch_text(url, referer, **options)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataSourceUnavailable("公开行情接口响应不是有效 JSON") from exc


def _fetch_text(url: str, referer: str, *, encoding="utf-8", timeout=None, retries=None):
    headers = {
        "User-Agent": os.getenv("PUBLIC_DATA_USER_AGENT", "Mozilla/5.0"),
        "Accept": "application/json,text/plain,*/*",
        "Referer": referer,
    }
    last_error = None
    retries = _request_retries() if retries is None else retries
    for attempt in range(retries + 1):
        request = Request(url, headers=headers)
        _wait_for_request_slot()
        try:
            with urlopen(request, timeout=_request_timeout() if timeout is None else timeout) as response:
                text = response.read().decode(encoding, errors="replace")
                _persist_raw_response(url, text)
                break
        except OSError as exc:
            last_error = exc
            permanent = isinstance(exc, HTTPError) and exc.code in {400, 401, 403, 404, 405, 410, 501}
            if permanent or attempt >= retries:
                raise DataSourceUnavailable(f"公开行情接口请求失败：{exc}") from exc
            time.sleep(_retry_delay_seconds(attempt))
    else:
        raise DataSourceUnavailable(f"公开行情接口请求失败：{last_error}")
    return text


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
    if source == "tencent":
        with _ARCHIVE_LOCK:
            _tencent_archive_index.cache_clear()


def _source_name(url: str) -> str:
    if "sina.com.cn" in url:
        return "sina"
    if "gtimg.cn" in url or "qq.com" in url:
        return "tencent"
    return "public"


def _sina_kline_url(market_symbol: str, days: int | None = None) -> str:
    query = urlencode(
        {
            "symbol": market_symbol,
            "scale": 240,
            "ma": "no",
            "datalen": days if days is not None else int(os.getenv("PUBLIC_KLINE_DAYS", str(DEFAULT_KLINE_DAYS))),
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


def _sina_stock_list_url(page: int, page_size: int, node: str = "hs_a", *,
                         sort: str = "symbol", ascending: bool = True) -> str:
    query = urlencode(
        {
            "page": page,
            "num": page_size,
            "sort": sort,
            "asc": int(ascending),
            "node": node,
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
