from __future__ import annotations

import io
import json
import unittest
from math import sin
from pathlib import Path
from html.parser import HTMLParser
from unittest.mock import Mock, patch

from web import QuantWebHandler
from analysis.ml_model import ALGORITHMS
from analysis.advisor import make_no_data_advice
from test_model_evaluation import _bar


class WebRequestValidationTest(unittest.TestCase):
    def test_single_stock_endpoint_serializes_real_new_model_results(self):
        """Given 本地合成行情 When 单股接口运行新增算法 Then 返回真实训练结果及风险分析而非静态示例"""
        bars = [_bar(i, price * .998, price * 1.01, price * .99, price, 1000 + i % 7 * 40)
                for i in range(180) for price in [10 + sin(i / 5) * 1.2 + i * .005]]
        for algorithm in ("random_forest", "extra_trees", "gradient_boosting", "gaussian_nb"):
            with self.subTest(algorithm=algorithm):
                handler = object.__new__(QuantWebHandler)
                handler._read_json = Mock(return_value={"symbol": "600001", "algorithm": algorithm})
                handler._send_json = Mock()
                with patch("web.fetch_bars", return_value=bars), patch("analysis.service._safe_fund_flow", return_value=None):
                    handler._handle_advise()
                payload = handler._send_json.call_args.args[0]
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["advice"]["ml_prediction"]["algorithm"], algorithm)
                self.assertEqual(payload["advice"]["ml_prediction"]["neighbor_count"], 0)
                self.assertTrue(payload["advice"]["risks"])
                json.dumps(payload, allow_nan=False)

    def test_model_dependency_failure_is_reported_as_recoverable_error(self):
        """Given 运行环境缺少模型依赖 When 分析 Then 返回可读错误而不静默替换模型"""
        handler = object.__new__(QuantWebHandler)
        handler._read_json = Mock(return_value={"symbol": "600001", "algorithm": "random_forest"})
        handler._send_json = Mock()
        with patch("web.fetch_bars", return_value=[]), \
             patch("web.build_advice", side_effect=RuntimeError("模型依赖缺失")):
            handler._handle_advise()
        self.assertEqual(handler._send_json.call_args.kwargs["status"], 503)
        self.assertFalse(handler._send_json.call_args.args[0]["ok"])

    def test_each_algorithm_reaches_the_single_stock_analyzer(self):
        """Given 选择某算法 When 请求单股分析 Then 后端原样执行该算法并拒绝未知算法"""
        for algorithm in (*ALGORITHMS, "unknown_model"):
            with self.subTest(algorithm=algorithm):
                handler = object.__new__(QuantWebHandler)
                handler._read_json = Mock(return_value={"symbol": "600001", "algorithm": algorithm})
                handler._send_json = Mock()
                with patch("web.fetch_bars", return_value=[]) as fetch, \
                     patch("web.build_advice", return_value=make_no_data_advice("样本不足")) as build:
                    handler._handle_advise()
                if algorithm in ALGORITHMS:
                    build.assert_called_once_with("600001", [], algorithm)
                    self.assertTrue(handler._send_json.call_args.args[0]["ok"])
                else:
                    fetch.assert_not_called()
                    build.assert_not_called()
                    self.assertEqual(handler._send_json.call_args.kwargs["status"], 400)

    def test_single_stock_selector_matches_backend_algorithm_catalog(self):
        """Given 后端算法目录 When 展示单股选择框 Then 全部可选算法代码与后端一致"""
        class SelectorParser(HTMLParser):
            active = False
            values = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "select": self.active = attrs.get("id") == "algorithm"
                if tag == "option" and self.active: self.values.append(attrs["value"])

            def handle_endtag(self, tag):
                if tag == "select": self.active = False

        parser = SelectorParser()
        parser.feed((Path(__file__).parents[1] / "src/web_static/index.html").read_text())
        self.assertEqual(set(parser.values), set(ALGORITHMS))
        self.assertEqual(len(parser.values), len(ALGORITHMS))

    def test_invalid_utf8_is_reported_as_invalid_json(self) -> None:
        handler = object.__new__(QuantWebHandler)
        handler.headers = {"Content-Length": "1"}
        handler.rfile = io.BytesIO(b"\xff")

        with self.assertRaises(json.JSONDecodeError):
            handler._read_json()

    def test_non_standard_json_constants_are_rejected(self) -> None:
        raw_body = b'{"initial_cash": NaN}'
        handler = object.__new__(QuantWebHandler)
        handler.headers = {"Content-Length": str(len(raw_body))}
        handler.rfile = io.BytesIO(raw_body)

        with self.assertRaises(json.JSONDecodeError):
            handler._read_json()


if __name__ == "__main__":
    unittest.main()
