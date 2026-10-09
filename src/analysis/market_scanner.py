from __future__ import annotations

import os
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from analysis.scanner import ScanResult
from analysis.advisor import Advice
from analysis.service import build_advice
from data.errors import DataRefreshError, DataSourceUnavailable
from data.public_sources import (
    fetch_public_a_stock_gainers, fetch_public_a_stock_losers,
    fetch_public_a_stock_limit_pool, fetch_public_market_stock_bars,
)


UNIVERSE_LIMIT = 500
MARKET_POOLS = {
    "gainers": ("涨幅 Top 500", 500, "新浪公开涨幅榜"),
    "losers": ("跌幅 Top 500", 500, "新浪公开跌幅榜"),
    "limit_down": ("跌停最多 100 只", 100, "东方财富公开跌停池"),
    "limit_up": ("涨停最多 100 只", 100, "东方财富公开涨停池"),
}


@dataclass(frozen=True)
class MarketScanSnapshot:
    running: bool
    status: str
    status_message: str
    version: int
    fetched_count: int
    skipped_count: int
    failed_count: int
    scanned_count: int
    total_count: int
    top_results: list[dict]
    last_started_at: str
    last_finished_at: str
    error: str
    cached_count: int = 0
    last_completed: str = ""
    industry_status: str = ""
    start_version: int = 0
    universe_limit: int = UNIVERSE_LIMIT
    universe_source: str = "新浪公开涨幅榜"
    universe_fetched_at: str = ""
    pool: str = "gainers"
    universe_date: str = ""
    universe_shortfall: int = 0
    successful_count: int = 0
    deferred_count: int = 0


class MarketScanner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._run_id = 0
        self._running = False
        self._status = "idle"
        self._version = 0
        self._fetched_count = 0
        self._skipped_count = 0
        self._failed_count = 0
        self._deferred_count = 0
        self._scanned_count = 0
        self._total_count = 0
        self._top_results: list[ScanResult] = []
        self._last_started_at = ""
        self._last_finished_at = ""
        self._error = ""
        self._cached_count = 0
        self._last_completed = ""
        self._processed: set[Future] = set()
        self._updates: deque[MarketScanSnapshot] = deque(maxlen=8192)
        self._cache: dict[tuple, tuple[float, ScanResult | None]] = {}
        self._start_version = 0
        self._universe_ranks: dict[str, int] = {}
        self._universe_fetched_at = ""
        self._pool = "gainers"
        self._universe_date = ""
        self._worker_active = False
        self._pending_request = None
        self._executor: ThreadPoolExecutor | None = None
        self._universe_executor: ThreadPoolExecutor | None = None
        self._inflight: set[Future] = set()

    def start(self, algorithm: str, top_n: int, pool: str = "gainers") -> None:
        if not isinstance(pool, str) or pool not in MARKET_POOLS:
            raise ValueError("候选股票池不支持。")
        top_n = max(1, min(10, top_n))
        with self._lock:
            if self._running and pool == self._pool and not self._stop_event.is_set():
                return
            self._running = True
            self._run_id += 1
            run_id = self._run_id
            self._stop_event.set()
            self._stop_event = threading.Event()
            self._pool = pool
            self._status = "loading_universe"
            self._top_results = []
            self._last_started_at = _format_time(time.time())
            self._last_finished_at = ""
            self._error = ""
            self._fetched_count = self._skipped_count = self._failed_count = 0
            self._deferred_count = 0
            self._scanned_count = self._total_count = self._cached_count = 0
            self._last_completed = ""
            self._universe_ranks.clear()
            self._universe_fetched_at = ""
            self._universe_date = ""
            self._processed.clear()
            self._updates.clear()
            self._start_version = self._version + 1
            self._mark_changed_locked()
            self._pending_request = (run_id, self._stop_event, algorithm, top_n, pool)
            if self._executor is None:
                workers = max(1, min(16, int(os.getenv("MARKET_SCAN_WORKERS", "8"))))
                self._executor = ThreadPoolExecutor(max_workers=workers)
                self._universe_executor = ThreadPoolExecutor(max_workers=2)
            if not self._worker_active:
                self._worker_active = True
                self._thread = threading.Thread(target=self._run_loop, daemon=True)
                self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._running = False
            self._status = "stopped"
            self._mark_changed_locked()

    def snapshot(self) -> MarketScanSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def wait_for_update(self, version: int, timeout: float) -> MarketScanSnapshot:
        with self._changed:
            self._changed.wait_for(lambda: self._version != version, timeout=timeout)
            for item in self._updates:
                if item.version > version:
                    return item
            return self._snapshot_locked()

    def _snapshot_locked(self) -> MarketScanSnapshot:
        from data.industry import INDUSTRIES
        results = [{**asdict(result), **INDUSTRIES.lookup(result.symbol),
                    "universe_rank": self._universe_ranks.get(result.symbol)} for result in self._top_results]
        return MarketScanSnapshot(
                running=self._running,
                status=self._status,
                status_message=self._status_message_locked(),
                version=self._version,
                fetched_count=self._fetched_count,
                skipped_count=self._skipped_count,
                failed_count=self._failed_count,
                scanned_count=self._scanned_count,
                total_count=self._total_count,
                top_results=results,
                last_started_at=self._last_started_at,
                last_finished_at=self._last_finished_at,
                error=self._error,
                cached_count=self._cached_count,
                last_completed=self._last_completed,
                industry_status=INDUSTRIES.status(),
                start_version=self._start_version,
                pool=self._pool,
                universe_limit=MARKET_POOLS[self._pool][1],
                universe_source=MARKET_POOLS[self._pool][2],
                universe_fetched_at=self._universe_fetched_at,
                universe_date=self._universe_date,
                universe_shortfall=max(0, MARKET_POOLS[self._pool][1] - self._total_count)
                    if self._universe_fetched_at else 0,
                successful_count=max(0, self._scanned_count - self._failed_count),
                deferred_count=self._deferred_count,
            )

    def _mark_changed_locked(self, *, completed: bool = False) -> None:
        self._version += 1
        if completed:
            self._updates.append(self._snapshot_locked())
        self._changed.notify_all()

    def refresh_industries(self) -> None:
        with self._lock:
            self._mark_changed_locked()

    def _status_message_locked(self) -> str:
        if self._status == "idle":
            return "待启动"
        if self._status == "loading_universe":
            return f"正在读取{MARKET_POOLS[self._pool][0]}"
        if self._status == "scanning":
            message = (
                f"分析中，已处理 {self._scanned_count}/{self._total_count}，"
                f"有效 {self._scanned_count - self._failed_count}，失败 {self._failed_count}，"
                f"待重试 {self._deferred_count}，板块排除 {self._skipped_count}"
            )
            if self._error:
                return f"{message}，数据提示：{self._error[:60]}"
            return message
        if self._status == "data_unavailable":
            return f"在线行情源暂不可用，{self._deferred_count} 只待重试；已保留 {self._scanned_count - self._failed_count} 只有效分析"
        if self._status == "finished":
            if self._fetched_count == 0:
                return "当日股票池为空，暂无可分析股票"
            if self._total_count == 0:
                return "池内股票均被板块规则排除，暂无可分析股票"
            if self._failed_count and self._failed_count == self._scanned_count:
                return "行情全部获取失败，本轮未形成有效分析"
            if self._failed_count:
                return f"本轮筛选结束，{self._failed_count} 只失败，覆盖不完整"
            return "本轮筛选完成"
        if self._status == "stopping":
            return "停止中"
        if self._status == "stopped":
            return "已停止"
        if self._status == "error":
            return f"错误：{self._error or '未知错误'}"
        return self._status

    def _run_loop(self) -> None:
        # Keep one coordinator and bounded shared executors across cancelled generations.
        while True:
            with self._lock:
                request = self._pending_request
                self._pending_request = None
            run_id, stop_event, algorithm, top_n, pool = request
            try:
                if not stop_event.is_set():
                    self._run_once(run_id, stop_event, algorithm, top_n, pool)
            except Exception:
                with self._lock:
                    if self._run_id == run_id and not stop_event.is_set():
                        self._status = "error"
                        self._error = "本轮筛选失败，请稍后重试。"
            with self._lock:
                if self._run_id == run_id:
                    self._running = False
                    if stop_event.is_set() and self._status != "error":
                        self._status = "stopped"
                    self._mark_changed_locked()
                if self._pending_request is None:
                    self._worker_active = False
                    self._close_idle_executors_locked()
                    return

    def _submit(self, executor, operation, *args) -> Future:
        with self._lock:
            future = executor.submit(operation, *args)
            self._inflight.add(future)
        future.add_done_callback(self._release_finished_task)
        return future

    def _release_finished_task(self, future: Future) -> None:
        with self._changed:
            self._inflight.discard(future)
            self._close_idle_executors_locked()
            self._changed.notify_all()

    def _close_idle_executors_locked(self) -> None:
        if self._worker_active or self._inflight:
            return
        for executor in (self._executor, self._universe_executor):
            if executor is not None:
                executor.shutdown(wait=False)
        self._executor = self._universe_executor = None

    def _run_once(
        self,
        run_id: int,
        stop_event: threading.Event,
        algorithm: str,
        top_n: int,
        pool: str = "gainers",
    ) -> bool:
        with self._lock:
            if self._run_id != run_id or stop_event.is_set():
                return False
            self._running = True
            self._status = "loading_universe"
            self._fetched_count = 0
            self._skipped_count = 0
            self._failed_count = 0
            self._deferred_count = 0
            self._scanned_count = 0
            self._total_count = 0
            self._top_results = []
            self._last_started_at = _format_time(time.time())
            self._last_finished_at = ""
            self._error = ""
            self._cached_count = 0
            self._last_completed = ""
            self._mark_changed_locked()

        workers = max(1, min(16, int(os.getenv("MARKET_SCAN_WORKERS", "8"))))
        task_timeout = max(5, float(os.getenv("MARKET_TASK_TIMEOUT_SECONDS", "30")))
        universe_future = None
        pending: set[Future] = set()
        submitted_at: dict[Future, float] = {}
        try:
            # Quotas count eligible stocks; retain the inspected prefix for source ranks and exclusions.
            universe_future = self._submit(self._universe_executor, _load_market_universe, pool, stop_event)
            with self._changed:
                self._changed.wait_for(lambda: universe_future.done() or stop_event.is_set())
                if stop_event.is_set():
                    return False
            stocks, universe_date = universe_future.result()
            eligible_stocks, ranks = [], {}
            fetched_count = skipped_count = 0
            for rank, stock in enumerate(stocks, 1):
                fetched_count = rank
                if _is_excluded_market_symbol(stock.symbol):
                    skipped_count += 1
                    continue
                eligible_stocks.append(stock)
                ranks[stock.symbol] = rank
                if len(eligible_stocks) == MARKET_POOLS[pool][1]:
                    break
            with self._lock:
                if self._run_id != run_id or stop_event.is_set():
                    return False
                self._universe_ranks = ranks
                self._universe_fetched_at = _format_time(time.time())
                self._universe_date = universe_date
                self._fetched_count = fetched_count
                self._skipped_count = skipped_count
                self._total_count = len(eligible_stocks)
                self._status = "scanning"
                self._mark_changed_locked()
            for stock in eligible_stocks:
                if stop_event.is_set() or not self._is_current_run(run_id):
                    break
                future = self._submit(self._executor, self._analyze_cached, stock.symbol, stock.name, algorithm, stop_event)
                pending.add(future)
                submitted_at[future] = time.time()
                # Publish each result immediately without waiting for the remaining stocks.
                future.add_done_callback(lambda done, current=run_id: self._record_finished_future(done, top_n, current))

                while len(pending) >= workers and not stop_event.is_set() and self._is_current_run(run_id):
                    pending = self._drain_finished(pending, submitted_at, top_n, task_timeout, wait_for_one=True, run_id=run_id)

            while pending and not stop_event.is_set() and self._is_current_run(run_id):
                pending = self._drain_finished(pending, submitted_at, top_n, task_timeout, wait_for_one=True, run_id=run_id)
        except DataRefreshError as exc:
            with self._lock:
                if self._run_id != run_id or stop_event.is_set():
                    return False
                self._error = str(exc)
                self._status = "error"
                self._running = False
                self._mark_changed_locked()
            return False
        finally:
            if universe_future is not None:
                universe_future.cancel()
            for future in pending:
                future.cancel()

        with self._lock:
            if self._run_id != run_id or stop_event.is_set():
                return False
            if self._fetched_count == 0 and pool in {"gainers", "losers"}:
                self._error = "未读取到涨跌幅前 500 名股票池，请重试；不会回退全市场扫描"
                self._status = "error"
                self._running = False
                self._mark_changed_locked()
                return False
            self._last_finished_at = _format_time(time.time())
            self._status = "data_unavailable" if self._deferred_count else "finished"
            self._mark_changed_locked()
            return True

    def _is_current_run(self, run_id: int) -> bool:
        with self._lock:
            return self._run_id == run_id

    def _analyze_cached(
        self, symbol: str, name: str, algorithm: str, stop_event: threading.Event | None = None,
    ) -> ScanResult | None:
        if stop_event is not None and stop_event.is_set():
            return None
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        session = (now.date(), now.hour >= 15, os.getenv("PUBLIC_KLINE_DAYS", "260"))
        key = (symbol, name, algorithm, session)
        with self._lock:
            cached = self._cache.get(key)
        ttl = 900 if cached and cached[1] and cached[1].action != "NO_DATA" else 60
        if cached and 0 <= time.monotonic() - cached[0] < ttl:
            return replace(cached[1], cached=True) if cached[1] else None
        result = _analyze_market_stock(symbol, name, algorithm)
        with self._lock:
            if stop_event is not None and stop_event.is_set():
                return None
            if len(self._cache) >= 6000:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (time.monotonic(), result)
        return result

    def _drain_finished(
        self,
        pending: set[Future],
        submitted_at: dict[Future, float],
        top_n: int,
        task_timeout: float,
        wait_for_one: bool,
        run_id: int | None = None,
    ) -> set[Future]:
        if not pending:
            return pending
        timeout = .05 if wait_for_one else 0
        done, remaining = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
        for future in done:
            submitted_at.pop(future, None)
            self._record_finished_future(future, top_n, run_id)
        return self._remove_timed_out_futures(remaining, submitted_at, task_timeout, run_id)

    def _remove_timed_out_futures(
        self,
        pending: set[Future],
        submitted_at: dict[Future, float],
        task_timeout: float,
        run_id: int | None = None,
    ) -> set[Future]:
        now = time.time()
        remaining = set()
        for future in pending:
            started_at = submitted_at.get(future, now)
            if now - started_at < task_timeout:
                remaining.add(future)
                continue
            self._record_timeout_future(future, run_id)
            if future.cancel():
                submitted_at.pop(future, None)
            else:
                # A Python thread cannot be killed: retain its slot to avoid an unbounded request queue.
                remaining.add(future)
        return remaining

    def _record_finished_future(self, future: Future, top_n: int, run_id: int | None = None) -> None:
        error = ""
        unavailable = False
        try:
            result = future.result()
        except DataSourceUnavailable as exc:
            result = None
            error = str(exc)
            unavailable = True
        except Exception as exc:
            result = None
            error = str(exc)
        with self._lock:
            if (future in self._processed or run_id is not None
                    and (run_id != self._run_id or not self._running or self._stop_event.is_set())):
                return
            self._processed.add(future)
            if error:
                self._error = error
            if unavailable:
                self._deferred_count += 1
                self._mark_changed_locked(completed=True)
                return
            self._scanned_count += 1
            self._cached_count += int(bool(result and result.cached))
            self._last_completed = f"{result.name} {result.symbol}" if result else "行情不足或请求失败"
            if result and result.action in {"BUY", "WATCH", "HOLD", "AVOID"}:
                self._top_results.append(result)
                self._top_results.sort(key=lambda item: (item.score, item.probability), reverse=True)
                self._top_results = self._top_results[:max(1, min(10, top_n))]
            else:
                self._failed_count += 1
                if result is not None:
                    self._error = result.reason
            self._mark_changed_locked(completed=True)

    def _record_timeout_future(self, future: Future | None = None, run_id: int | None = None) -> None:
        with self._lock:
            if (future in self._processed or run_id is not None
                    and (run_id != self._run_id or not self._running or self._stop_event.is_set())):
                return
            if future is not None:
                self._processed.add(future)
            self._scanned_count += 1
            self._failed_count += 1
            self._error = "部分股票请求超时，已跳过"
            self._last_completed = "请求超时"
            self._mark_changed_locked(completed=True)


def _load_market_universe(pool: str, stop_event: threading.Event):
    if stop_event.is_set():
        return [], ""
    options = {"include_symbol": lambda symbol: not _is_excluded_market_symbol(symbol),
               "should_stop": stop_event.is_set}
    if pool == "gainers":
        return fetch_public_a_stock_gainers(**options), ""
    if pool == "losers":
        return fetch_public_a_stock_losers(**options), ""
    trade_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return fetch_public_a_stock_limit_pool(
        "up" if pool == "limit_up" else "down", trade_date=trade_date, **options,
    ), trade_date.isoformat()


def _analyze_market_stock(symbol: str, name: str, algorithm: str) -> ScanResult | None:
    try:
        bars, source = fetch_public_market_stock_bars(symbol, name)
    except DataSourceUnavailable:
        raise
    except DataRefreshError as exc:
        return ScanResult(symbol, name, "NO_DATA", 0, 0, str(exc), "行情缺失，未形成买入建议。")
    advice = build_advice(symbol, bars, algorithm)
    if not advice.indicators or not advice.ml_prediction:
        return None
    as_of = datetime.fromisoformat(str(advice.indicators.analysis_date)).date()
    if not 0 <= (datetime.now(ZoneInfo("Asia/Shanghai")).date() - as_of).days <= 7:
        return None
    risks = list(advice.risks)
    if advice.fund_flow is None:
        risks.append("资金流未参与评分。")
    if "本地缓存" in source:
        risks.append("在线行情不可用，本次使用已校验的同日同时段缓存，非实时行情。")
    return ScanResult(
        symbol=symbol,
        name=advice.indicators.name or "未知",
        action=advice.action,
        score=advice.score,
        probability=advice.ml_prediction.buy_probability,
        reason=_summarize_market_reason(advice),
        risk=_summarize_market_risk(advice, source),
        analysis_date=as_of.isoformat(), price=advice.indicators.close, quote_source=source,
        reasons=tuple(advice.reasons), evidence=tuple(advice.evidence),
        risks=tuple(risks), observations=tuple(advice.observations),
    )


def _is_excluded_market_symbol(symbol: str) -> bool:
    return symbol.startswith(("300", "301", "688", "689", "4", "8", "9"))


def _summarize_market_risk(advice: Advice, source: str) -> str:
    patterns = advice.patterns
    signals = [
        (not advice.ml_prediction or advice.ml_prediction.buy_probability < .45, "模型信号偏弱"),
        (patterns and patterns.volume_drop_below_ma20, "放量破位"),
        (advice.divergence and advice.divergence.bearish_divergence, "顶背驰"),
        (patterns and patterns.low_liquidity, "流动性偏低"),
        (patterns and patterns.high_volume_stagnation, "放量滞涨"),
        (patterns and patterns.high_20d_return, "追高回撤风险"),
        (advice.fund_flow and advice.fund_flow.fund_flow_score_adjustment < 0, "资金流偏弱"),
        (patterns and patterns.price_down_volume_up and not patterns.volume_drop_below_ma20, "放量下跌"),
        (patterns and patterns.macd_dead_cross, "MACD死叉"),
        (patterns and patterns.macd_histogram_shrinking and not patterns.macd_dead_cross, "MACD动能减弱"),
        (patterns and not patterns.close_above_ma20 and not patterns.volume_drop_below_ma20, "未站上20日线"),
        (patterns and not patterns.ma20_up, "20日线未走强"),
        (patterns and advice.indicators and advice.indicators.ma250 > 0 and not patterns.close_above_ma250,
         "未站上年线"),
    ]
    risks = [text for confirmed, text in signals if confirmed][:2] or ["仍需控制仓位"]
    if advice.fund_flow is None:
        risks.append("未计资金流")
    if "本地缓存" in source:
        risks.append("缓存非实时")
    return "；".join(risks) + "。"


def _summarize_market_reason(advice: Advice) -> str:
    if advice.action != "BUY":
        return {
            "WATCH": "买入确认不足，等待趋势与量能配合。",
            "HOLD": "信号中性，暂无明确买入理由。",
            "AVOID": "买入证据不足，暂不买入。",
        }.get(advice.action, "数据不足，无法判断买入条件。")
    patterns = advice.patterns
    if patterns is None:
        return "模型达到买入观察门槛，量价仍待确认。"
    signals = [
        (patterns.yearline_pullback_low_volume, "年线上方缩量回踩"),
        (patterns.breakout_with_volume, "放量突破20日高点"),
        (patterns.yearline_breakout_with_volume, "年线上方放量走强"),
        (patterns.first_day_high_breakout_with_volume, "放量突破首日高点"),
        (patterns.pullback_low_volume, "缩量回调且守住20日均线"),
        (patterns.macd_golden_cross_above_zero, "MACD零轴上方金叉"),
        (patterns.macd_golden_cross and not patterns.macd_golden_cross_above_zero, "MACD金叉，动能修复"),
        (advice.fund_flow and advice.fund_flow.fund_flow_score_adjustment > 0, "资金流指标偏正面"),
        (advice.divergence and advice.divergence.bullish_divergence, "出现下跌动能衰减信号"),
        (patterns.low_position_rebound, "低位出现止跌反弹信号"),
        (patterns.close_above_ma20 and patterns.ma20_up, "站上20日均线且均线向上"),
        (patterns.macd_histogram_expanding and not patterns.macd_golden_cross, "MACD动能增强"),
    ]
    reasons = [text for confirmed, text in signals if confirmed][:2]
    return "；".join(reasons) + "。" if reasons else "模型达到买入观察门槛，量价仍待确认。"


def _format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


MARKET_SCANNER = MarketScanner()
