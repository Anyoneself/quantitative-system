from __future__ import annotations

from dataclasses import asdict, dataclass

from data.models import PriceVolumeBar


@dataclass(frozen=True)
class ChanPoint:
    kind: str
    index: int
    trade_date: str
    price: float


@dataclass(frozen=True)
class ChanStroke:
    direction: str
    start_index: int
    end_index: int
    start_price: float
    end_price: float


@dataclass(frozen=True)
class ChanCenter:
    start_index: int
    end_index: int
    upper: float
    lower: float
    middle: float


@dataclass(frozen=True)
class ChanStructureResult:
    trend: str
    position: str
    buy_signal: str
    risk_signal: str
    score_adjustment: int
    recommendation: str
    explanation: str
    points: list[ChanPoint]
    strokes: list[ChanStroke]
    center: ChanCenter | None

    def to_dict(self) -> dict:
        payload = asdict(self)
        return payload


def analyze_chan_structure(bars: list[PriceVolumeBar]) -> ChanStructureResult:
    if len(bars) < 12:
        return ChanStructureResult(
            trend="不确定",
            position="数据不足",
            buy_signal="无",
            risk_signal="无",
            score_adjustment=0,
            recommendation="缠论结构样本不足，暂不参与买入判断。",
            explanation="至少需要 12 个交易日才能形成初步分型和笔。",
            points=[],
            strokes=[],
            center=None,
        )

    points = _detect_fractals(bars)
    strokes = _build_strokes(points)
    center = _find_latest_center(strokes)
    trend = _classify_trend(strokes)
    position = _classify_position(bars[-1].close, center)
    buy_signal = _classify_buy_signal(bars, strokes, center, trend, position)
    risk_signal = _classify_risk_signal(bars, strokes, center, trend, position)
    score_adjustment = _score_adjustment(trend, buy_signal, risk_signal)
    recommendation = _recommendation(trend, position, buy_signal, risk_signal, score_adjustment)
    explanation = _explanation(trend, position, buy_signal, risk_signal, center)

    return ChanStructureResult(
        trend=trend,
        position=position,
        buy_signal=buy_signal,
        risk_signal=risk_signal,
        score_adjustment=score_adjustment,
        recommendation=recommendation,
        explanation=explanation,
        points=points,
        strokes=strokes,
        center=center,
    )


def _detect_fractals(bars: list[PriceVolumeBar]) -> list[ChanPoint]:
    points: list[ChanPoint] = []
    for index in range(1, len(bars) - 1):
        previous = bars[index - 1]
        current = bars[index]
        following = bars[index + 1]
        if current.high > previous.high and current.high > following.high and current.low > previous.low and current.low > following.low:
            points.append(ChanPoint("top", index, current.trade_date.isoformat(), current.high))
        elif current.low < previous.low and current.low < following.low and current.high < previous.high and current.high < following.high:
            points.append(ChanPoint("bottom", index, current.trade_date.isoformat(), current.low))
    return _dedupe_adjacent_points(points)


def _dedupe_adjacent_points(points: list[ChanPoint]) -> list[ChanPoint]:
    selected: list[ChanPoint] = []
    for point in points:
        if not selected:
            selected.append(point)
            continue
        previous = selected[-1]
        if point.kind != previous.kind:
            selected.append(point)
            continue
        if point.kind == "top" and point.price > previous.price:
            selected[-1] = point
        elif point.kind == "bottom" and point.price < previous.price:
            selected[-1] = point
    return selected


def _build_strokes(points: list[ChanPoint]) -> list[ChanStroke]:
    strokes: list[ChanStroke] = []
    for start, end in zip(points, points[1:]):
        if end.index - start.index < 2:
            continue
        if start.kind == "bottom" and end.kind == "top" and end.price > start.price:
            strokes.append(ChanStroke("up", start.index, end.index, start.price, end.price))
        elif start.kind == "top" and end.kind == "bottom" and end.price < start.price:
            strokes.append(ChanStroke("down", start.index, end.index, start.price, end.price))
    return strokes


def _find_latest_center(strokes: list[ChanStroke]) -> ChanCenter | None:
    if len(strokes) < 3:
        return None
    for offset in range(len(strokes) - 3, -1, -1):
        window = strokes[offset : offset + 3]
        lower = max(min(stroke.start_price, stroke.end_price) for stroke in window)
        upper = min(max(stroke.start_price, stroke.end_price) for stroke in window)
        if lower <= upper:
            return ChanCenter(
                start_index=window[0].start_index,
                end_index=window[-1].end_index,
                upper=upper,
                lower=lower,
                middle=(upper + lower) / 2,
            )
    return None


def _classify_trend(strokes: list[ChanStroke]) -> str:
    if len(strokes) < 2:
        return "不确定"
    recent = strokes[-3:]
    up_count = sum(1 for stroke in recent if stroke.direction == "up")
    down_count = len(recent) - up_count
    if up_count > down_count and recent[-1].end_price > recent[0].start_price:
        return "上升结构"
    if down_count > up_count and recent[-1].end_price < recent[0].start_price:
        return "下降结构"
    return "盘整结构"


def _classify_position(close: float, center: ChanCenter | None) -> str:
    if center is None:
        return "未形成中枢"
    center_width = max(center.upper - center.lower, center.middle * 0.01)
    if close > center.upper:
        if close - center.upper <= center_width * 0.45:
            return "中枢上沿附近"
        return "离开中枢上方"
    if close < center.lower:
        if center.lower - close <= center_width * 0.45:
            return "中枢下沿附近"
        return "跌破中枢下方"
    return "中枢内震荡"


def _classify_buy_signal(
    bars: list[PriceVolumeBar],
    strokes: list[ChanStroke],
    center: ChanCenter | None,
    trend: str,
    position: str,
) -> str:
    if center is None or not strokes:
        return "无"
    close = bars[-1].close
    recent_low = min(bar.low for bar in bars[-8:])
    if position in {"中枢上沿附近", "离开中枢上方"} and close >= center.upper and trend in {"上升结构", "盘整结构"}:
        return "三买候选"
    if trend != "下降结构" and recent_low >= center.lower * 0.98 and close >= center.middle:
        return "二买候选"
    return "无"


def _classify_risk_signal(
    bars: list[PriceVolumeBar],
    strokes: list[ChanStroke],
    center: ChanCenter | None,
    trend: str,
    position: str,
) -> str:
    close = bars[-1].close
    if center and close < center.lower:
        return "跌破中枢候选"
    if trend == "下降结构":
        return "下降结构延续"
    if _has_top_divergence(bars, strokes):
        return "顶背驰候选"
    if position == "离开中枢上方" and _recent_volume_shrinks(bars):
        return "高位缩量候选"
    return "无"


def _has_top_divergence(bars: list[PriceVolumeBar], strokes: list[ChanStroke]) -> bool:
    up_strokes = [stroke for stroke in strokes if stroke.direction == "up"]
    if len(up_strokes) < 2:
        return False
    previous = up_strokes[-2]
    current = up_strokes[-1]
    previous_gain = previous.end_price - previous.start_price
    current_gain = current.end_price - current.start_price
    close_is_high = bars[-1].close >= max(bar.close for bar in bars[-20:]) * 0.98
    return close_is_high and current.end_price > previous.end_price and current_gain < previous_gain * 0.75


def _recent_volume_shrinks(bars: list[PriceVolumeBar]) -> bool:
    if len(bars) < 20:
        return False
    recent_volume = sum(bar.volume for bar in bars[-5:]) / 5
    base_volume = sum(bar.volume for bar in bars[-20:]) / 20
    return recent_volume < base_volume * 0.82


def _score_adjustment(trend: str, buy_signal: str, risk_signal: str) -> int:
    score = 0
    if buy_signal == "三买候选":
        score += 8
    elif buy_signal == "二买候选":
        score += 6
    if trend == "上升结构":
        score += 4
    elif trend == "下降结构":
        score -= 8
    if risk_signal == "顶背驰候选":
        score -= 10
    elif risk_signal == "跌破中枢候选":
        score -= 12
    elif risk_signal == "下降结构延续":
        score -= 8
    elif risk_signal == "高位缩量候选":
        score -= 5
    return score


def _recommendation(trend: str, position: str, buy_signal: str, risk_signal: str, score_adjustment: int) -> str:
    if risk_signal != "无" and score_adjustment < 0:
        return f"缠论结构提示 {risk_signal}，当前不适合追买，优先等待结构修复。"
    if buy_signal in {"二买候选", "三买候选"} and score_adjustment > 0:
        return f"缠论结构出现 {buy_signal}，可作为买入观察加分项，但仍需结合量价评分和仓位控制。"
    if trend == "盘整结构":
        return "当前更像震荡整理，适合等待突破或回踩确认。"
    return f"当前处于{position}，缠论结构未给出强买点，建议观察。"


def _explanation(trend: str, position: str, buy_signal: str, risk_signal: str, center: ChanCenter | None) -> str:
    center_text = "尚未形成可用中枢"
    if center:
        center_text = f"最近中枢区间约为 {center.lower:.2f} - {center.upper:.2f}"
    return (
        f"{center_text}；当前结构为{trend}，位置为{position}，"
        f"买点标记为{buy_signal}，风险标记为{risk_signal}。"
    )