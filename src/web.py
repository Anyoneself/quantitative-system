from __future__ import annotations

import argparse
import json
import errno
from dataclasses import asdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from analysis.ml_model import ALGORITHMS
from analysis.market_scanner import MARKET_SCANNER
from analysis.scanner import parse_symbols, scan_top_stocks
from analysis.service import analyze_stock_sell, build_advice
from data.errors import DataRefreshError
from data.source import fetch_bars
from output.formatter import advice_to_dict


STATIC_DIR = Path(__file__).with_name("web_static")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="股票量价分析 Web 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    server = _create_server(args.host, args.port)
    host, port = server.server_address
    print(f"Web 服务已启动：http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def _create_server(host: str, port: int) -> ThreadingHTTPServer:
    candidate_ports = [port]
    if port == 8000:
        candidate_ports.extend(range(8001, 8011))

    for candidate_port in candidate_ports:
        try:
            return ThreadingHTTPServer((host, candidate_port), QuantWebHandler)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            if port != 8000:
                raise SystemExit(
                    f"端口 {candidate_port} 已被占用，请换一个端口，例如：--port {candidate_port + 1}；"
                    "也可以使用 --port 0 自动选择空闲端口。"
                ) from exc

    raise SystemExit("端口 8000-8010 都已被占用，请使用 --port 0 自动选择空闲端口。")


class QuantWebHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self) -> None:
        if self.path == "/api/advise":
            self._handle_advise()
            return
        if self.path == "/api/sell-advice":
            self._handle_sell_advice()
            return
        if self.path == "/api/scan":
            self._handle_scan()
            return
        if self.path == "/api/market-scan/start":
            self._handle_market_scan_start()
            return
        if self.path == "/api/market-scan/stop":
            MARKET_SCANNER.stop()
            self._send_json({"ok": True})
            return
        self.send_error(404)

    def do_GET(self) -> None:
        if self.path == "/api/market-scan/status":
            self._send_json({"ok": True, "snapshot": asdict(MARKET_SCANNER.snapshot())})
            return
        if self.path == "/api/market-scan/stream":
            self._handle_market_scan_stream()
            return
        super().do_GET()

    def _handle_advise(self) -> None:
        try:
            payload = self._read_json()
            symbol = _normalize_symbol(str(payload.get("symbol", "")))
            algorithm = str(payload.get("algorithm", "knn"))
            if not symbol:
                self._send_json({"ok": False, "error": "股票代码必须是 6 位数字。"}, status=400)
                return
            if algorithm not in ALGORITHMS:
                self._send_json({"ok": False, "error": "机器学习算法不支持。"}, status=400)
                return

            bars = fetch_bars(symbol)
            advice = build_advice(symbol, bars, algorithm)
            chart_bars = bars[-90:]
            self._send_json(
                {
                    "ok": True,
                    "advice": advice_to_dict(advice),
                    "chart": _chart_payload(chart_bars),
                    "chan": advice.chan_structure.to_dict() if advice.chan_structure else None,
                    "beginner": _beginner_payload(advice),
                }
            )
        except DataRefreshError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=502)
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "请求不是有效 JSON。"}, status=400)

    def _handle_sell_advice(self) -> None:
        try:
            payload = self._read_json()
            symbol = _normalize_symbol(str(payload.get("symbol", "")))
            if not symbol:
                self._send_json({"ok": False, "error": "股票代码必须是 6 位数字。"}, status=400)
                return
            cost_price = _parse_optional_float(payload.get("cost_price"))
            quantity = _parse_optional_float(payload.get("quantity"))
            max_loss_rate = _parse_float(payload.get("max_loss_rate"), 0.08)
            target_profit_rate = _parse_float(payload.get("target_profit_rate"), 0.20)
            sell_advice = analyze_stock_sell(symbol, cost_price, quantity, max_loss_rate, target_profit_rate)
            self._send_json({"ok": True, "sell_advice": asdict(sell_advice)})
        except DataRefreshError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=502)
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "请求不是有效 JSON。"}, status=400)

    def _handle_scan(self) -> None:
        try:
            payload = self._read_json()
            algorithm = str(payload.get("algorithm", "knn"))
            symbols = parse_symbols(str(payload.get("symbols", "")).replace("\n", ","))
            top_n = _parse_int(payload.get("top"), 10)
            min_score = _parse_int(payload.get("min_score"), 52)
            if algorithm not in ALGORITHMS:
                self._send_json({"ok": False, "error": "机器学习算法不支持。"}, status=400)
                return
            if not symbols:
                self._send_json({"ok": False, "error": "请输入股票池。"}, status=400)
                return
            invalid_symbols = [symbol for symbol in symbols if not _normalize_symbol(symbol)]
            if invalid_symbols:
                self._send_json({"ok": False, "error": f"股票代码格式不正确：{', '.join(invalid_symbols)}"}, status=400)
                return

            results = scan_top_stocks(
                symbols=symbols,
                algorithm=algorithm,
                top_n=top_n,
                min_score=min_score,
            )
            self._send_json(
                {
                    "ok": True,
                    "results": [asdict(result) for result in results],
                    "scanned_count": len(symbols),
                    "top": top_n,
                    "min_score": min_score,
                }
            )
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "请求不是有效 JSON。"}, status=400)

    def _handle_market_scan_start(self) -> None:
        try:
            payload = self._read_json()
            algorithm = str(payload.get("algorithm", "knn"))
            top_n = _parse_int(payload.get("top"), 10)
            if algorithm not in ALGORITHMS:
                self._send_json({"ok": False, "error": "机器学习算法不支持。"}, status=400)
                return
            MARKET_SCANNER.start(algorithm, top_n)
            self._send_json({"ok": True, "snapshot": asdict(MARKET_SCANNER.snapshot())})
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "请求不是有效 JSON。"}, status=400)

    def _handle_market_scan_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        snapshot = MARKET_SCANNER.snapshot()
        version = snapshot.version
        if not self._send_event(snapshot):
            return

        while True:
            snapshot = MARKET_SCANNER.wait_for_update(version, 25)
            version = snapshot.version
            if not self._send_event(snapshot):
                return

    def _read_json(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        return json.loads(raw_body.decode("utf-8"))

    def _send_event(self, snapshot) -> bool:
        body = json.dumps({"ok": True, "snapshot": asdict(snapshot)}, ensure_ascii=False)
        try:
            self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return False
        return True

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return


def _normalize_symbol(symbol: str) -> str:
    cleaned = symbol.strip()
    if cleaned.isdigit() and len(cleaned) == 6:
        return cleaned
    return ""


def _parse_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_optional_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _chart_payload(bars) -> dict:
    return {
        "dates": [bar.trade_date.isoformat() for bar in bars],
        "highs": [bar.high for bar in bars],
        "lows": [bar.low for bar in bars],
        "closes": [bar.close for bar in bars],
        "volumes": [bar.volume for bar in bars],
    }


def _beginner_payload(advice) -> dict:
    action_map = {
        "BUY": "可以进入买入观察，但仍建议控制仓位",
        "WATCH": "暂时观察，等待更强确认",
        "HOLD": "信号中性，不急着买入",
        "AVOID": "不建议下一个交易日买进",
        "NO_DATA": "数据不足，不能判断",
    }
    return {
        "headline": action_map.get(advice.action, advice.action),
        "plain_language": [
            "机器学习概率不是收益承诺，更准确地说，是历史相似量价样本里的上涨占比。",
            "判断是否可信时，优先同时看趋势、成交量、20 日位置和风险提示，不能只看概率。",
            "如果概率低但趋势理由偏强，说明技术面有亮点，但历史相似样本并不支持立刻买。",
            "新手更适合把结论当成筛选器，再结合仓位、止损和市场环境做决定。",
        ],
        "next_steps": [
            "不要满仓买入单只股票。",
            "优先观察次日是否放量上涨，而不是只看开盘涨跌。",
            "若跌破 20 日均线或放量下跌，应降低买入意愿。",
            "每次交易前先确定最大可承受亏损。",
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
