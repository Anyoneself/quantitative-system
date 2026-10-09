from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
import time

from data.errors import DataRefreshError
from data.public_sources import _fetch_json, _fetch_text, _sina_stock_list_url


SOURCE = "新浪行业分类"


def _fetch_sectors():
    text = _fetch_text("https://money.finance.sina.com.cn/q/view/newFLJK.php?param=industry",
                       "https://finance.sina.com.cn/", encoding="gb18030", timeout=4, retries=0)
    prefix, separator, body = text.partition("=")
    if not separator or prefix.strip() != "var S_Finance_bankuai_industry":
        raise DataRefreshError("行业列表格式异常")
    try:
        payload = json.loads(body.strip().removesuffix(";"))
        sectors = []
        for code, value in payload.items():
            fields = value.split(",")
            if not re.fullmatch(r"hangye_[A-Z0-9]+", code) or fields[0] != code or not fields[1].strip():
                raise ValueError("invalid sector")
            sectors.append((code, fields[1].strip()))
        if not sectors:
            raise ValueError("empty sectors")
        return sectors
    except (ValueError, TypeError, AttributeError, IndexError) as exc:
        raise DataRefreshError("行业列表格式异常") from exc


def _fetch_sector_members(node):
    seen = set()
    for page in range(1, 101):
        rows = _fetch_json(_sina_stock_list_url(page, 100, node), "https://finance.sina.com.cn/",
                           timeout=4, retries=0)
        if not isinstance(rows, list):
            raise DataRefreshError("行业成分格式异常")
        symbols = {str(row.get("code", "")) for row in rows if isinstance(row, dict)}
        if any(not re.fullmatch(r"[0-9]{6}", symbol) for symbol in symbols) or len(symbols) != len(rows):
            raise DataRefreshError("行业成分代码异常")
        if symbols and not symbols - seen:
            raise DataRefreshError("行业接口重复返回同一页")
        seen.update(symbols)
        if len(rows) < 100:
            return sorted(seen)
    raise DataRefreshError("行业分页超过上限")


class IndustryDirectory:
    """Bulk public metadata only; no account-derived symbol is sent to this provider."""

    def __init__(self, path=None):
        self.path = Path(path) if path else Path("data/cache/industries.json")
        self._lock = threading.Lock()
        self._items = {}
        self._as_of = ""
        self._updated = 0.0
        self._data_at = 0.0
        self._last_attempt = 0.0
        self._state = "尚未更新"
        self._running = False
        self._thread = None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload["source"] == SOURCE and isinstance(payload["items"], dict):
                self._items = {key: value for key, value in payload["items"].items()
                               if re.fullmatch(r"[0-9]{6}", key) and isinstance(value, str)}
                self._as_of = payload["as_of"]
                self._data_at = datetime.fromisoformat(self._as_of).timestamp()
                self._updated = self._data_at if payload.get("complete") else 0
                self._state = "已缓存"
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def lookup(self, symbol):
        with self._lock:
            fresh = 0 <= time.time() - self._data_at < 86400
            industry = self._items.get(symbol, "") if fresh else ""
            state = ("READY" if industry else "CONFLICT" if symbol in self._items and fresh
                     else "PENDING" if self._running else "UNAVAILABLE")
            return {"industry": industry, "industry_source": SOURCE,
                    "industry_as_of": self._as_of, "industry_state": state}

    def status(self):
        with self._lock:
            return self._state

    def refresh(self, on_update=None):
        with self._lock:
            now = time.time()
            if self._running or 0 <= now - self._updated < 86400 or now - self._last_attempt < 60:
                return
            self._last_attempt = now
            self._running = True
            self._state = "行业更新中"
            self._thread = threading.Thread(target=self._load, args=(on_update,), daemon=True)
            self._thread.start()

    def _load(self, on_update):
        errors = 0
        items = {}
        as_of = datetime.now(timezone.utc).isoformat()
        try:
            sectors = _fetch_sectors()
            with ThreadPoolExecutor(max_workers=4) as pool:
                jobs = {pool.submit(_fetch_sector_members, node): name for node, name in sectors}
                for future in as_completed(jobs):
                    try:
                        members = future.result()
                    except Exception:
                        errors += 1
                        continue
                    name = jobs[future]
                    for symbol in members:
                        items[symbol] = name if symbol not in items or items[symbol] == name else ""
                    with self._lock:
                        self._items = dict(items)
                        self._as_of = as_of
                        self._data_at = datetime.fromisoformat(as_of).timestamp()
                        # Partial refreshes are visible but must not suppress retries after a failed run.
                    if on_update:
                        on_update()
            with self._lock:
                self._updated = time.time() if not errors else 0
                self._state = "行业已更新" if not errors else "部分行业获取失败"
            if items:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"source": SOURCE, "as_of": as_of, "items": items,
                                                 "complete": not errors}, ensure_ascii=False), encoding="utf-8")
                temporary.replace(self.path)
        except Exception:
            with self._lock:
                self._state = "行业获取失败"
        finally:
            with self._lock:
                self._running = False
            if on_update:
                on_update()


INDUSTRIES = IndustryDirectory()
