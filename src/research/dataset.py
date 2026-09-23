from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

from data.models import PriceVolumeBar
from data.quality import validate_price_bars


@dataclass(frozen=True)
class UniverseSnapshot:
    effective_date: date
    known_at: date
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class ResearchDataset:
    bars: dict[str, list[PriceVolumeBar]]
    universe: tuple[UniverseSnapshot, ...]
    benchmark: str
    source: str
    synthetic: bool
    adjustment: str

    def members_on(self, as_of: date) -> tuple[str, ...]:
        # 证券池必须来自当时已知的快照，不能用今天仍上市的股票倒推历史全集。
        eligible = [item for item in self.universe if item.effective_date <= as_of and item.known_at <= as_of]
        return eligible[-1].symbols if eligible else ()

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "source": self.source,
            "synthetic": self.synthetic,
            "adjustment": self.adjustment,
            "volume_unit": "shares",
            "benchmark": self.benchmark,
            "universe": [
                {
                    "effective_date": item.effective_date.isoformat(),
                    "known_at": item.known_at.isoformat(),
                    "symbols": list(item.symbols),
                }
                for item in self.universe
            ],
            "bars": {
                symbol: [{**asdict(bar), "trade_date": bar.trade_date.isoformat()} for bar in bars]
                for symbol, bars in sorted(self.bars.items())
            },
        }

    def fingerprint(self) -> str:
        return sha256(canonical_json(self.to_dict()).encode()).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def load_dataset(path: str | Path) -> ResearchDataset:
    path = Path(path)
    if path.stat().st_size > 20_000_000:
        raise ValueError("单个研究快照不能超过 20 MB。")
    return parse_dataset(json.loads(path.read_text(encoding="utf-8")))


def parse_dataset(payload: dict) -> ResearchDataset:
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload.get("schema_version") != 1:
        raise ValueError("研究快照 schema_version 必须为 1。")
    if not isinstance(payload.get("synthetic"), bool):
        raise ValueError("快照必须明确 synthetic 布尔标记。")
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip() or len(source) > 2000:
        raise ValueError("快照必须提供明确的数据和历史证券池来源。")
    if payload.get("adjustment") not in {"none", "qfq"} or payload.get("volume_unit") != "shares":
        raise ValueError("必须声明 none/qfq 价格口径，并将成交量统一为 shares（股）。")
    raw_bars = payload.get("bars")
    if not isinstance(raw_bars, dict) or not 2 <= len(raw_bars) <= 21:
        raise ValueError("研究快照需包含 1 到 20 只证券和一个独立基准。")
    bars = {}
    for symbol, rows in raw_bars.items():
        _validate_symbol(symbol)
        if not isinstance(rows, list) or not 2 <= len(rows) <= 3000:
            raise ValueError(f"{symbol} 日线条数必须在 2 到 3000 之间。")
        try:
            parsed = [PriceVolumeBar(**{**row, "trade_date": _date(row["trade_date"])}) for row in rows]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{symbol} 日线字段不完整或含未知字段。") from exc
        if any(not isinstance(bar.name, str) or len(bar.name) > 100 for bar in parsed):
            raise ValueError("证券名称必须是长度不超过 100 的字符串。")
        bars[symbol] = validate_price_bars(parsed)
        if any(max(bar.open, bar.high, bar.low, bar.close) > 1e9 or bar.volume > 1e12 or bar.amount > 1e16 for bar in parsed):
            raise ValueError("行情价格、成交量或成交额超过研究安全上限。")
    benchmark = payload.get("benchmark")
    _validate_symbol(benchmark)
    if benchmark not in bars:
        raise ValueError("快照缺少基准行情。")
    raw_universe = payload.get("universe")
    if not isinstance(raw_universe, list) or not 1 <= len(raw_universe) <= 3000:
        raise ValueError("必须提供历史证券池快照，不能直接使用当前上市证券列表。")
    universe = []
    for item in raw_universe:
        try:
            effective = _date(item["effective_date"])
            known = _date(item["known_at"])
            symbols = item["symbols"]
        except (KeyError, TypeError) as exc:
            raise ValueError("证券池需 effective_date、known_at 和 symbols。") from exc
        if not isinstance(symbols, list) or any(not isinstance(symbol, str) for symbol in symbols):
            raise ValueError("证券池 symbols 必须是字符串列表。")
        if len(symbols) != len(set(symbols)) or any(symbol not in bars or symbol == benchmark for symbol in symbols):
            raise ValueError("证券池不能重复、包含基准或缺少行情的证券。")
        if known > effective or (universe and effective <= universe[-1].effective_date):
            raise ValueError("证券池生效日必须严格递增，且不能早于信息已知日。")
        universe.append(UniverseSnapshot(effective, known, tuple(sorted(symbols))))
    # 基准提供主交易日历，拒绝把证券的额外日期静默丢弃。
    calendar = {bar.trade_date for bar in bars[benchmark]}
    if any(bar.trade_date not in calendar for symbol in bars if symbol != benchmark for bar in bars[symbol]):
        raise ValueError("证券日期必须包含在基准交易日历内。")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if any(bar.trade_date == now.date() and now.hour < 15 for rows in bars.values() for bar in rows):
        raise ValueError("快照包含尚未收盘的当日日线。")
    return ResearchDataset(bars, tuple(universe), benchmark, source.strip(), payload["synthetic"], payload["adjustment"])


def build_demo_dataset() -> ResearchDataset:
    randomizer = random.Random(20260923)
    days = []
    current = date(2023, 1, 3)
    while len(days) < 780:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    symbols = ("600001", "600002", "600003", "600004", "600005", "600006", "000300")
    common_returns = [randomizer.gauss(0.0002, 0.008) for _ in days]
    bars = {}
    for index, symbol in enumerate(symbols):
        price = 10 + index * 4
        rows = []
        for day_index, day in enumerate(days):
            regime = 0.0007 * math.sin(day_index / 65 + index)
            change = common_returns[day_index] * 0.6 + randomizer.gauss(0.0002 + regime, 0.008)
            open_price = round(price * (1 + randomizer.gauss(0, 0.002)), 2)
            close = round(open_price * (1 + change), 2)
            volume = randomizer.randint(3_000_000, 8_000_000)
            rows.append(PriceVolumeBar(
                name=f"合成样本{index + 1}", trade_date=day, open=open_price,
                high=round(max(open_price, close) * 1.008, 2),
                low=round(min(open_price, close) * 0.992, 2),
                close=close, volume=volume, amount=round(close * volume, 2),
            ))
            price = close
        bars[symbol] = rows
    return ResearchDataset(
        bars, (UniverseSnapshot(days[0], days[0], symbols[:-1]),), "000300",
        "确定性合成数据，仅用于工程验收；日期不是中国交易所日历。", True, "none",
    )


def _date(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("日期必须为 YYYY-MM-DD 字符串。")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value or parsed > datetime.now(ZoneInfo("Asia/Shanghai")).date():
        raise ValueError("日期格式错误或晚于当前日期。")
    return parsed


def _validate_symbol(symbol: object) -> None:
    if not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit():
        raise ValueError("证券代码必须是 6 位 ASCII 数字。")
