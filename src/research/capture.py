from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from data.public_sources import SHANGHAI_INDEX_SYMBOLS, _exclude_incomplete_current_bar, _fetch_sina_stock_bars
from data.quality import validate_price_bars
from research.dataset import ResearchDataset, UniverseSnapshot, _date, _validate_symbol, parse_dataset


def fetch_history(symbol: str, days: int) -> list:
    # 研究路径固定新浪未复权口径，不把腾讯的成交量口径或复权序列作为静默兜底。
    return validate_price_bars(_exclude_incomplete_current_bar(_fetch_sina_stock_bars(symbol, None, days)),
                               minimum_count=2)


def capture_dataset(
    symbols: list[str], benchmark: str = "000300", *, days: int = 1200,
    now: datetime | None = None,
) -> ResearchDataset:
    now = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(ZoneInfo("Asia/Shanghai"))
    if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 20
            or any(not isinstance(symbol, str) for symbol in symbols) or len(set(symbols)) != len(symbols)):
        raise ValueError("证券池需 1 到 20 个不重复的证券代码。")
    if isinstance(days, bool) or not isinstance(days, int) or not 60 <= days <= 3000:
        raise ValueError("采集日数需为 60 到 3000 的整数。")
    _validate_symbol(benchmark)
    if benchmark not in SHANGHAI_INDEX_SYMBOLS or benchmark in symbols:
        raise ValueError("必须使用支持的独立宽基指数作为基准。")
    for symbol in symbols:
        _validate_symbol(symbol)
        if symbol in SHANGHAI_INDEX_SYMBOLS or not symbol.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
            raise ValueError("当前只支持沪深主板普通股票，不接受指数、基金或其他板块。")
    bars = {symbol: fetch_history(symbol, days) for symbol in [*sorted(symbols), benchmark]}
    dataset = ResearchDataset(
        bars, (UniverseSnapshot(now.date(), now.date(), tuple(sorted(symbols))),),
        benchmark, "新浪公开日线；未复权；成交量为股；成交额以收盘价乘成交量估算；"
        "证券池为本次用户选择，仅从登记日生效；历史公司行动和证券状态未核验。",
        False, "none",
    )
    return parse_dataset(dataset.to_dict())


def merge_observation(frozen: ResearchDataset, incoming: ResearchDataset) -> ResearchDataset:
    if (frozen.benchmark != incoming.benchmark or set(frozen.bars) != set(incoming.bars)
            or frozen.adjustment != incoming.adjustment or frozen.synthetic != incoming.synthetic):
        raise ValueError("证券、基准或数据口径与冻结快照不一致。")
    cutoff = frozen.bars[frozen.benchmark][-1].trade_date.isoformat()
    if bool(frozen.calendar) != bool(incoming.calendar):
        raise ValueError("不能新增或丢弃冻结的日历来源。")
    if frozen.calendar and (
        frozen.calendar["source"] != incoming.calendar["source"]
        or [day for day in frozen.calendar["sessions"] if day <= cutoff]
        != [day for day in incoming.calendar["sessions"] if day <= cutoff]
    ):
        raise ValueError("日历来源或已观察日期被改写。")
    bars = {}
    for symbol, rows in frozen.bars.items():
        existing = {row.trade_date: row for row in rows}
        observed = {row.trade_date: row for row in incoming.bars[symbol]}
        overlap = existing.keys() & observed.keys()
        if not overlap:
            raise ValueError(f"{symbol} 缺少重叠行情，无法核对历史一致性。")
        if any(existing[day] != observed[day] for day in overlap):
            raise ValueError(f"{symbol} 历史行情发生修订，冻结账本不能被重写。")
        # 不仅比较共有行，也拒绝删除或回填已观察区间中的旧行情。
        first, last, observed_first = min(existing), max(existing), min(observed)
        interval = {day for day in existing if observed_first <= day <= last}
        incoming_old = {day for day in observed if first <= day <= last}
        if interval != incoming_old:
            raise ValueError(f"{symbol} 历史行情日期被删除或回填。")
        # 更长采集窗口中的早期行情不进入已冻结输入；只追加登记区间之后的新日期。
        bars[symbol] = rows + [row for row in incoming.bars[symbol] if row.trade_date > rows[-1].trade_date]
    return parse_dataset(replace(frozen, bars=bars, calendar=incoming.calendar).to_dict())


def capture_historical_dataset(manifest: dict, benchmark: str = "000300", *, days: int = 1200) -> ResearchDataset:
    if (not isinstance(manifest, dict) or not {"source", "universe"} <= set(manifest)
            or set(manifest) - {"source", "universe", "calendar"}
            or not isinstance(manifest["source"], str) or not 1 <= len(manifest["source"].strip()) <= 1000
            or not isinstance(manifest["universe"], list) or not 1 <= len(manifest["universe"]) <= 3000):
        raise ValueError("历史证券池清单必须提供 source 和 universe，来源不能为空。")
    snapshots, symbols = [], set()
    for item in manifest["universe"]:
        if not isinstance(item, dict) or set(item) != {"effective_date", "known_at", "symbols"}:
            raise ValueError("历史证券池需 effective_date、known_at 和 symbols。")
        effective, known = _date(item["effective_date"]), _date(item["known_at"])
        members = item["symbols"]
        if not isinstance(members, list):
            raise ValueError("证券池 symbols 必须为列表。")
        for symbol in members:
            _validate_symbol(symbol)
        if len(members) != len(set(members)) or known > effective or (snapshots and effective <= snapshots[-1].effective_date):
            raise ValueError("历史证券池日期或成员无效。")
        snapshots.append(UniverseSnapshot(effective, known, tuple(sorted(members))))
        symbols.update(members)
    captured = capture_dataset(sorted(symbols), benchmark, days=days)
    source = ("新浪公开未复权日线；成交量为股；成交额为估算值。历史证券池为用户提供，未经独立核验："
              + manifest["source"].strip())
    return parse_dataset(replace(captured, universe=tuple(snapshots), source=source,
                                 calendar=manifest.get("calendar")).to_dict())
