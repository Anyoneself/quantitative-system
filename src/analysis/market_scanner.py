from __future__ import annotations

import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime

from analysis.scanner import ScanResult
from analysis.service import analyze_stock
from data.errors import DataRefreshError
from data.source import iter_a_stock_symbols


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
        self._scanned_count = 0
        self._total_count = 0
        self._top_results: list[ScanResult] = []
        self._last_started_at = ""
        self._last_finished_at = ""
        self._error = ""

    def start(self, algorithm: str, top_n: int) -> None:
        with self._lock:
            self._run_id += 1
            run_id = self._run_id
            self._stop_event.set()
            self._stop_event = threading.Event()
            self._status = "loading_universe"
            self._mark_changed_locked()
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(run_id, self._stop_event, algorithm, top_n),
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            self._status = "stopping"
            self._mark_changed_locked()

    def snapshot(self) -> MarketScanSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def wait_for_update(self, version: int, timeout: float) -> MarketScanSnapshot:
        with self._changed:
            self._changed.wait_for(lambda: self._version != version, timeout=timeout)
            return self._snapshot_locked()

    def _snapshot_locked(self) -> MarketScanSnapshot:
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
                top_results=[asdict(result) for result in self._top_results],
                last_started_at=self._last_started_at,
                last_finished_at=self._last_finished_at,
                error=self._error,
            )

    def _mark_changed_locked(self) -> None:
        self._version += 1
        self._changed.notify_all()

    def _status_message_locked(self) -> str:
        if self._status == "idle":
            return "待启动"
        if self._status == "loading_universe":
            return "正在抓取全 A 股票列表"
        if self._status == "scanning":
            return (
                f"分析中，已完成 {self._scanned_count}/{self._total_count}，"
                f"已跳过 {self._skipped_count}，失败 {self._failed_count}"
            )
        if self._status == "finished":
            return "本轮全量完成"
        if self._status == "stopping":
            return "停止中"
        if self._status == "stopped":
            return "已停止"
        if self._status == "error":
            return f"错误：{self._error or '未知错误'}"
        return self._status

    def _run_loop(
        self,
        run_id: int,
        stop_event: threading.Event,
        algorithm: str,
        top_n: int,
    ) -> None:
        self._run_once(run_id, stop_event, algorithm, top_n)

        with self._lock:
            if self._run_id != run_id:
                return
            self._running = False
            if stop_event.is_set() and self._status != "error":
                self._status = "stopped"
            self._mark_changed_locked()

    def _run_once(
        self,
        run_id: int,
        stop_event: threading.Event,
        algorithm: str,
        top_n: int,
    ) -> bool:
        with self._lock:
            if self._run_id != run_id:
                return False
            self._running = True
            self._status = "loading_universe"
            self._fetched_count = 0
            self._skipped_count = 0
            self._failed_count = 0
            self._scanned_count = 0
            self._total_count = 0
            self._top_results = []
            self._last_started_at = _format_time(time.time())
            self._last_finished_at = ""
            self._error = ""
            self._mark_changed_locked()

        workers = max(1, int(os.getenv("MARKET_SCAN_WORKERS", "4")))
        task_timeout = max(5, float(os.getenv("MARKET_TASK_TIMEOUT_SECONDS", "30")))
        executor = ThreadPoolExecutor(max_workers=workers)
        pending: set[Future] = set()
        submitted_at: dict[Future, float] = {}
        try:
            for stock in iter_a_stock_symbols():
                if stop_event.is_set() or not self._is_current_run(run_id):
                    break
                with self._lock:
                    if self._run_id != run_id:
                        break
                    self._fetched_count += 1
                    self._mark_changed_locked()
                if _is_excluded_market_symbol(stock.symbol):
                    with self._lock:
                        if self._run_id != run_id:
                            break
                        self._skipped_count += 1
                        self._mark_changed_locked()
                    continue

                future = executor.submit(_analyze_market_stock, stock.symbol, stock.name, algorithm)
                pending.add(future)
                submitted_at[future] = time.time()
                with self._lock:
                    if self._run_id != run_id:
                        break
                    self._total_count += 1
                    self._status = "scanning"
                    self._mark_changed_locked()

                while len(pending) >= workers and not stop_event.is_set() and self._is_current_run(run_id):
                    pending = self._drain_finished(pending, submitted_at, top_n, task_timeout, wait_for_one=True)

            while pending and not stop_event.is_set() and self._is_current_run(run_id):
                pending = self._drain_finished(pending, submitted_at, top_n, task_timeout, wait_for_one=True)
        except DataRefreshError as exc:
            with self._lock:
                if self._run_id != run_id:
                    return False
                self._error = str(exc)
                self._status = "error"
                self._running = False
                self._mark_changed_locked()
            return False
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        with self._lock:
            if self._run_id != run_id:
                return False
            if self._fetched_count == 0:
                self._error = "未从行情接口读取到股票列表，请检查网络、数据源配置或接口响应"
                self._status = "error"
                self._running = False
                self._mark_changed_locked()
                return False
            if self._total_count == 0:
                self._error = "过滤后没有可分析股票，请检查板块过滤规则或股票列表响应"
                self._status = "error"
                self._running = False
                self._mark_changed_locked()
                return False
            self._last_finished_at = _format_time(time.time())
            self._status = "finished"
            self._mark_changed_locked()
            return True

    def _is_current_run(self, run_id: int) -> bool:
        with self._lock:
            return self._run_id == run_id

    def _drain_finished(
        self,
        pending: set[Future],
        submitted_at: dict[Future, float],
        top_n: int,
        task_timeout: float,
        wait_for_one: bool,
    ) -> set[Future]:
        if not pending:
            return pending
        timeout = 1 if wait_for_one else 0
        done, remaining = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
        for future in done:
            submitted_at.pop(future, None)
            self._record_finished_future(future, top_n)
        return self._remove_timed_out_futures(remaining, submitted_at, task_timeout)

    def _remove_timed_out_futures(
        self,
        pending: set[Future],
        submitted_at: dict[Future, float],
        task_timeout: float,
    ) -> set[Future]:
        now = time.time()
        remaining = set()
        for future in pending:
            started_at = submitted_at.get(future, now)
            if now - started_at < task_timeout:
                remaining.add(future)
                continue
            future.cancel()
            submitted_at.pop(future, None)
            self._record_timeout_future()
        return remaining

    def _record_finished_future(self, future: Future, top_n: int) -> None:
        try:
            result = future.result()
        except Exception as exc:
            result = None
            with self._lock:
                self._error = str(exc)
        with self._lock:
            self._scanned_count += 1
            if result:
                self._top_results.append(result)
                self._top_results.sort(key=lambda item: (item.score, item.probability), reverse=True)
                self._top_results = self._top_results[:top_n]
            else:
                self._failed_count += 1
            self._mark_changed_locked()

    def _record_timeout_future(self) -> None:
        with self._lock:
            self._scanned_count += 1
            self._failed_count += 1
            self._error = "部分股票请求超时，已跳过"
            self._mark_changed_locked()


def _analyze_market_stock(symbol: str, name: str, algorithm: str) -> ScanResult | None:
    advice = analyze_stock(symbol, algorithm, name)
    if not advice.indicators or not advice.ml_prediction:
        return None
    return ScanResult(
        symbol=symbol,
        name=advice.indicators.name or "未知",
        action=advice.action,
        score=advice.score,
        probability=advice.ml_prediction.buy_probability,
        reason=_join_market_reason(advice.reasons, advice.evidence),
        risk=advice.risks[0] if advice.risks else "未发现主要量价风险",
    )


def _is_excluded_market_symbol(symbol: str) -> bool:
    return symbol.startswith(("300", "301", "688", "689", "4", "8", "9"))


def _join_market_reason(reasons: list[str], evidence: list[str]) -> str:
    items = []
    if reasons:
        items.append(reasons[0])
    items.extend(evidence[:2])
    return "；".join(items) if items else "无明确理由"


def _format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


MARKET_SCANNER = MarketScanner()
