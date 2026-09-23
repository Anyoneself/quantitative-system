from __future__ import annotations

import argparse
import errno
import json
import sqlite3
from dataclasses import asdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from api.portfolio import (
    build_create_portfolio_response,
    build_dashboard_response,
    build_portfolio_detail_response,
    build_portfolio_list_response,
    build_record_transaction_response,
    build_refresh_portfolio_response,
)
from api.research import build_backtest_response, build_evaluation_response
from api.experiments import build_experiment_response, list_experiments, load_experiment
from analysis.ml_model import ALGORITHMS
from analysis.market_scanner import MARKET_SCANNER
from analysis.scanner import parse_symbols, scan_top_stocks
from analysis.service import analyze_stock_sell, build_advice
from data.errors import DataRefreshError
from data.source import fetch_bars
from desktop.tonghuashun import BridgeError, TonghuashunBridge, validate_local_request
from output.formatter import advice_to_dict
from storage.migrations import initialize_database


STATIC_DIR = Path(__file__).with_name("web_static")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="股票量价分析 Web 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    initialize_database()
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
            server = ThreadingHTTPServer((host, candidate_port), QuantWebHandler)
            server.desktop_bridge = TonghuashunBridge()
            return server
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

    def end_headers(self) -> None:
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/client/"):
            self._handle_client_bridge(path.removeprefix("/api/client/"))
            return
        if path == "/api/advise":
            self._handle_advise()
            return
        if path == "/api/sell-advice":
            self._handle_sell_advice()
            return
        if path == "/api/scan":
            self._handle_scan()
            return
        if path == "/api/market-scan/start":
            self._handle_market_scan_start()
            return
        if path == "/api/market-scan/stop":
            MARKET_SCANNER.stop()
            self._send_json({"ok": True})
            return
        if path == "/api/evaluate":
            self._handle_evaluate()
            return
        if path == "/api/backtest":
            self._handle_backtest()
            return
        if path == "/api/experiments":
            self._handle_experiment(create=True)
            return
        if path == "/api/portfolios":
            self._handle_create_portfolio()
            return
        portfolio_route = _portfolio_route(path)
        if portfolio_route and portfolio_route[1] == "transactions":
            self._handle_record_transaction(portfolio_route[0])
            return
        if portfolio_route and portfolio_route[1] == "refresh":
            self._handle_refresh_portfolio(portfolio_route[0])
            return
        self.send_error(404)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/api/client":
            self._handle_client_bridge()
            return
        if path == "/api/health":
            self._handle_health()
            return
        if path == "/api/dashboard":
            self._handle_dashboard(parse_qs(parsed.query))
            return
        if path == "/api/portfolios":
            self._handle_list_portfolios()
            return
        if path == "/api/experiments" or path.startswith("/api/experiments/"):
            self._handle_experiment(run_id=path.removeprefix("/api/experiments/") if path != "/api/experiments" else None)
            return
        portfolio_route = _portfolio_route(path)
        if portfolio_route and portfolio_route[1] == "":
            self._handle_portfolio_detail(portfolio_route[0])
            return
        if path == "/api/market-scan/status":
            self._send_json({"ok": True, "snapshot": asdict(MARKET_SCANNER.snapshot())})
            return
        if path == "/api/market-scan/stream":
            self._handle_market_scan_stream()
            return
        super().do_GET()

    def _handle_client_bridge(self, action: str | None = None) -> None:
        bridge = self.server.desktop_bridge
        try:
            if len(self.headers.get_all("Host", [])) != 1:
                raise BridgeError("LOCAL_ONLY", "本机请求 Host 无效。")
            validate_local_request(
                bind_host=self.server.server_address[0], peer_host=self.client_address[0],
                port=self.server.server_address[1], headers=self.headers,
                mutation=action is not None, token=bridge.token,
            )
        except BridgeError as exc:
            self.close_connection = True
            self._send_json({"ok": False, "error": str(exc), "code": exc.code}, status=403)
            return
        try:
            if action is not None:
                self.connection.settimeout(5)
                lengths = self.headers.get_all("Content-Length", [])
                if (len(lengths) != 1 or not lengths[0].isdigit()
                        or not 0 < int(lengths[0]) <= 4096 or self.headers.get("Transfer-Encoding")):
                    raise ValueError("本机操作请求体无效。")
                payload = self._read_json()
                if payload:
                    raise ValueError("本机操作不接受登录凭据、URL 或其他参数。")
                state = bridge.perform(action)
            else:
                state = bridge.status()
            self._send_json({"ok": True, "connection": state})
        except BridgeError as exc:
            response = {"ok": False, "error": str(exc), "code": exc.code}
            if exc.code != "BUSY":
                response["connection"] = bridge.status()
            self._send_json(response, status=409 if exc.code in ("BUSY", "ACTION_UNAVAILABLE") else 422)
        except (ValueError, TimeoutError):
            self.close_connection = True
            self._send_json({"ok": False, "error": "本机操作请求无效。"}, status=400)

    def _handle_advise(self) -> None:
        try:
            payload = self._read_json()
            symbol = _normalize_symbol(str(payload.get("symbol", "")))
            algorithm = str(payload.get("algorithm", "robust_ensemble"))
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
                    "fund_flow": asdict(advice.fund_flow) if advice.fund_flow else None,
                    "divergence": asdict(advice.divergence) if advice.divergence else None,
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
            self._send_json(
                {
                    "ok": True,
                    "sell_advice": asdict(sell_advice),
                    "fund_flow_risk": asdict(sell_advice.fund_flow) if sell_advice.fund_flow else None,
                    "divergence_risk": asdict(sell_advice.divergence) if sell_advice.divergence else None,
                }
            )
        except DataRefreshError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=502)
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "请求不是有效 JSON。"}, status=400)

    def _handle_scan(self) -> None:
        try:
            payload = self._read_json()
            algorithm = str(payload.get("algorithm", "robust_ensemble"))
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
            algorithm = str(payload.get("algorithm", "robust_ensemble"))
            top_n = _parse_int(payload.get("top"), 10)
            if algorithm not in ALGORITHMS:
                self._send_json({"ok": False, "error": "机器学习算法不支持。"}, status=400)
                return
            MARKET_SCANNER.start(algorithm, top_n)
            self._send_json({"ok": True, "snapshot": asdict(MARKET_SCANNER.snapshot())})
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "请求不是有效 JSON。"}, status=400)

    def _handle_health(self) -> None:
        try:
            initialize_database()
            self._send_json(
                {
                    "ok": True,
                    "service": "quantitative-system",
                    "database": "ready",
                }
            )
        except sqlite3.Error as exc:
            self.log_error("数据库健康检查失败：%s", exc)
            self._send_json({"ok": False, "error": "数据库不可用。"}, status=503)

    def _handle_dashboard(self, query: dict[str, list[str]]) -> None:
        try:
            portfolio_id = _optional_portfolio_id(query.get("portfolio_id", [None])[0])
            self._send_json(build_dashboard_response(portfolio_id))
        except LookupError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=422)
        except sqlite3.Error as exc:
            self.log_error("仪表盘数据库读取失败：%s", exc)
            self._send_json(
                {"ok": False, "error": "仪表盘数据库读取失败。"},
                status=_database_error_status(exc),
            )

    def _handle_list_portfolios(self) -> None:
        try:
            self._send_json(build_portfolio_list_response())
        except sqlite3.Error as exc:
            self.log_error("组合列表读取失败：%s", exc)
            self._send_json(
                {"ok": False, "error": "组合列表读取失败。"},
                status=_database_error_status(exc),
            )

    def _handle_create_portfolio(self) -> None:
        try:
            self._send_json(build_create_portfolio_response(self._read_json()), status=201)
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "请求不是有效 JSON。"}, status=400)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=422)
        except sqlite3.Error as exc:
            self.log_error("创建组合失败：%s", exc)
            self._send_json(
                {"ok": False, "error": "创建组合失败。"},
                status=_database_error_status(exc),
            )

    def _handle_portfolio_detail(self, portfolio_id: int) -> None:
        try:
            self._send_json(build_portfolio_detail_response(portfolio_id))
        except LookupError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=422)
        except sqlite3.Error as exc:
            self.log_error("组合详情读取失败：%s", exc)
            self._send_json(
                {"ok": False, "error": "组合详情读取失败。"},
                status=_database_error_status(exc),
            )

    def _handle_record_transaction(self, portfolio_id: int) -> None:
        try:
            response = build_record_transaction_response(portfolio_id, self._read_json())
            self._send_json(
                response,
                status=200 if response.get("idempotent_replay") else 201,
            )
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "请求不是有效 JSON。"}, status=400)
        except LookupError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except ValueError as exc:
            status = (
                409
                if (
                    "现金不足" in str(exc)
                    or "超过持仓" in str(exc)
                    or "同一 request_id 已用于不同" in str(exc)
                )
                else 422
            )
            self._send_json({"ok": False, "error": str(exc)}, status=status)
        except DataRefreshError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=502)
        except sqlite3.Error as exc:
            self.log_error("记录交易失败：%s", exc)
            self._send_json(
                {"ok": False, "error": "记录交易失败。"},
                status=_database_error_status(exc),
            )

    def _handle_refresh_portfolio(self, portfolio_id: int) -> None:
        try:
            self._send_json(build_refresh_portfolio_response(portfolio_id))
        except LookupError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=422)
        except sqlite3.Error as exc:
            self.log_error("刷新组合失败：%s", exc)
            self._send_json(
                {"ok": False, "error": "刷新组合失败。"},
                status=_database_error_status(exc),
            )

    def _handle_evaluate(self) -> None:
        try:
            self._send_json(build_evaluation_response(self._read_json()))
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "请求不是有效 JSON。"}, status=400)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=422)
        except DataRefreshError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=502)

    def _handle_backtest(self) -> None:
        try:
            self._send_json(build_backtest_response(self._read_json()))
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "请求不是有效 JSON。"}, status=400)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=422)
        except DataRefreshError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=502)

    def _handle_experiment(self, create: bool = False, run_id: str | None = None) -> None:
        try:
            if create:
                self._send_json(build_experiment_response(self._read_json()), status=201)
            elif run_id is not None:
                self._send_json({"ok": True, "experiment": load_experiment(run_id)})
            else:
                self._send_json({"ok": True, "experiments": list_experiments()})
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "JSON 请求或报告无效。"}, status=400)
        except (ValueError, DataRefreshError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=422)
        except LookupError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except RuntimeError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=409)
        except OSError:
            self._send_json({"ok": False, "error": "实验文件读取或留档失败。"}, status=503)

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
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise json.JSONDecodeError("Content-Length 格式不正确", "", 0) from exc
        if content_length <= 0 or content_length > 1_048_576:
            raise json.JSONDecodeError("请求体为空或过大", "", 0)
        raw_body = self.rfile.read(content_length)
        try:
            decoded_body = raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise json.JSONDecodeError("请求体必须使用 UTF-8 编码", "", 0) from exc
        payload = json.loads(
            decoded_body,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(payload, dict):
            raise json.JSONDecodeError("JSON 顶层必须是对象", "", 0)
        return payload

    def _send_event(self, snapshot) -> bool:
        body = json.dumps({"ok": True, "snapshot": asdict(snapshot)}, ensure_ascii=True)
        try:
            self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return False
        return True

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
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


def _reject_json_constant(value: str) -> None:
    raise json.JSONDecodeError(f"JSON 不允许常量 {value}", value, 0)


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


def _portfolio_route(path: str) -> tuple[int, str] | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) not in {3, 4} or parts[:2] != ["api", "portfolios"]:
        return None
    try:
        portfolio_id = int(parts[2])
    except ValueError:
        return None
    if portfolio_id <= 0:
        return None
    action = parts[3] if len(parts) == 4 else ""
    if action not in {"", "transactions", "refresh"}:
        return None
    return portfolio_id, action


def _optional_portfolio_id(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        portfolio_id = int(value)
    except ValueError as exc:
        raise ValueError("portfolio_id 必须是正整数。") from exc
    if portfolio_id <= 0:
        raise ValueError("portfolio_id 必须是正整数。")
    return portfolio_id


def _database_error_status(exc: sqlite3.Error) -> int:
    if isinstance(exc, sqlite3.OperationalError):
        return 503
    return 500


def _chart_payload(bars) -> dict:
    return {
        "dates": [bar.trade_date.isoformat() for bar in bars],
        "opens": [bar.open for bar in bars],
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
