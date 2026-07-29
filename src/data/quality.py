from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from zoneinfo import ZoneInfo

from data.errors import DataQualityError
from data.models import PriceVolumeBar


def validate_price_bars(
    bars: list[PriceVolumeBar],
    *,
    minimum_count: int = 1,
) -> list[PriceVolumeBar]:
    if minimum_count < 1:
        raise ValueError("最少行情数量必须大于 0。")
    if len(bars) < minimum_count:
        raise DataQualityError(f"有效日线不足，至少需要 {minimum_count} 条，实际只有 {len(bars)} 条。")

    previous_date: date | None = None
    for index, bar in enumerate(bars):
        _validate_bar(bar, index)
        if previous_date is not None and bar.trade_date <= previous_date:
            if bar.trade_date == previous_date:
                raise DataQualityError(f"日线存在重复交易日期：{bar.trade_date.isoformat()}。")
            raise DataQualityError(
                f"日线必须按交易日期严格升序排列：{bar.trade_date.isoformat()} "
                f"早于 {previous_date.isoformat()}。"
            )
        previous_date = bar.trade_date
    return bars


def _validate_bar(bar: PriceVolumeBar, index: int) -> None:
    if not isinstance(bar.trade_date, date):
        raise DataQualityError(f"第 {index + 1} 条日线的交易日期无效。")
    if bar.trade_date > datetime.now(ZoneInfo("Asia/Shanghai")).date():
        raise DataQualityError(
            f"{bar.trade_date.isoformat()} 晚于上海市场当前日期。"
        )

    price_fields = {
        "开盘价": bar.open,
        "最高价": bar.high,
        "最低价": bar.low,
        "收盘价": bar.close,
    }
    for field_name, value in price_fields.items():
        if not _is_finite_number(value) or value <= 0:
            raise DataQualityError(
                f"{bar.trade_date.isoformat()} 的{field_name}必须是大于 0 的有限数。"
            )

    if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
        raise DataQualityError(
            f"{bar.trade_date.isoformat()} 的最高价或最低价不能覆盖开盘价和收盘价。"
        )
    if bar.low > bar.high:
        raise DataQualityError(f"{bar.trade_date.isoformat()} 的最低价不能高于最高价。")
    if not _is_finite_number(bar.volume) or bar.volume < 0:
        raise DataQualityError(f"{bar.trade_date.isoformat()} 的成交量不能为负数或非有限数。")
    if not _is_finite_number(bar.amount) or bar.amount < 0:
        raise DataQualityError(f"{bar.trade_date.isoformat()} 的成交额不能为负数或非有限数。")


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
