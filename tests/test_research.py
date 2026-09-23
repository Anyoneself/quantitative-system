from __future__ import annotations

import copy
import io
import json
import math
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from api.experiments import build_experiment_response, list_experiments, load_experiment
from cli import main
from data.models import PriceVolumeBar
from data.errors import DataRefreshError
from research.config import SimulationConfig, StrategyConfig, ValidationConfig, parse_config
from research.dataset import ResearchDataset, UniverseSnapshot, build_demo_dataset, load_dataset, parse_dataset
from research.engine import simulate_portfolio
from research.execution import simulate_fill
from research.experiment import block_bootstrap_interval, run_experiment, save_experiment
from research.strategy import target_weights


STRATEGY = StrategyConfig(
    lookback=3, trend_window=4, volatility_window=3, rebalance_days=1,
    max_positions=2, max_weight=0.4, gross_limit=0.8, target_volatility=0.8, min_average_amount=0,
)
SIMULATION = SimulationConfig(commission_rate=0, stamp_duty_rate=0, min_commission=0, slippage_rate=0, participation_rate=0.1)


def dataset_fixture(count: int = 180, trend: float = 0.01) -> ResearchDataset:
    days = [date(2023, 1, 3) + timedelta(days=index) for index in range(count)]
    bars = {}
    for symbol in ("600001", "600002", "000300"):
        rows = []
        for index, day in enumerate(days):
            price = round(10 + index * trend, 2)
            rows.append(PriceVolumeBar("测试证券", day, price, price + 0.2, price - 0.2, price, 1_000_000, price * 1_000_000))
        bars[symbol] = rows
    return ResearchDataset(bars, (UniverseSnapshot(days[0], days[0], ("600001", "600002")),),
                           "000300", "单元测试合成样本", True, "none")


class ResearchDatasetTest(unittest.TestCase):
    def test_snapshot_round_trip_is_stable(self):
        dataset = dataset_fixture()
        self.assertEqual(parse_dataset(dataset.to_dict()).fingerprint(), dataset.fingerprint())

    def test_membership_requires_known_and_effective_dates(self):
        dataset = dataset_fixture()
        days = [bar.trade_date for bar in dataset.bars["000300"]]
        dataset = replace(dataset, universe=(
            UniverseSnapshot(days[3], days[2], ("600001",)),
            UniverseSnapshot(days[7], days[6], ("600002",)),
        ))
        self.assertEqual(dataset.members_on(days[2]), ())
        self.assertEqual(dataset.members_on(days[6]), ("600001",))
        self.assertEqual(dataset.members_on(days[7]), ("600002",))

    def test_invalid_data_is_rejected(self):
        original = dataset_fixture().to_dict()
        mutations = [
            lambda item: item.update(synthetic="false"),
            lambda item: item.update(benchmark=[]),
            lambda item: item.update(schema_version=True),
            lambda item: item.update(volume_unit="lots"),
            lambda item: item.update(universe=[]),
            lambda item: item["universe"][0].update(known_at="2023-02-01"),
            lambda item: item["universe"][0].update(symbols=["600001", "600001"]),
            lambda item: item["bars"]["600001"][0].update(close=math.nan),
            lambda item: item["bars"]["600001"][0].update(volume=1e100),
            lambda item: item["bars"]["600001"][0].update(trade_date="2023-01-04"),
        ]
        for mutate in mutations:
            payload = copy.deepcopy(original)
            mutate(payload)
            with self.subTest(mutate=mutate), self.assertRaises((ValueError, DataRefreshError)):
                parse_dataset(payload)

    def test_current_pool_cannot_be_backdated_by_runner(self):
        dataset = dataset_fixture()
        day = dataset.bars["000300"][-1].trade_date
        dataset = replace(dataset, universe=(UniverseSnapshot(day, day, ("600001",)),))
        with self.assertRaisesRegex(ValueError, "当前股票池回填"):
            run_experiment(dataset, STRATEGY, SIMULATION, ValidationConfig(train_days=60, test_days=20))

    def test_config_rejects_nan_boolean_unknown_and_fractional_windows(self):
        for payload in (
            {"strategy": {"max_weight": math.nan}},
            {"simulation": {"initial_cash": True}},
            {"strategy": {"lookback": 2.5}},
            {"validation": {"bootstrap_samples": 999999}},
            {"strategy": {"fake": 1}}, {"foo": {}},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_config(payload)
        self.assertEqual(parse_config({"simulation": {"initial_cash": 100000.0}})[1].initial_cash, 100000)


class ResearchExecutionTest(unittest.TestCase):
    def setUp(self):
        self.dataset = dataset_fixture(trend=0)
        self.previous, self.bar = self.dataset.bars["600001"][:2]

    def test_t_plus_one_and_unsupported_board(self):
        fill = simulate_fill("600001", "SELL", 100, self.bar, self.previous, 1000, SIMULATION, self.bar.trade_date)
        self.assertEqual(fill.reason, "T_PLUS_ONE")
        self.assertEqual(simulate_fill("688001", "BUY", 100, self.bar, self.previous, 1000, SIMULATION).reason, "UNSUPPORTED_BOARD")

    def test_capacity_partial_fill_and_cash_constraint(self):
        bar = replace(self.bar, volume=3000)
        fill = simulate_fill("600001", "BUY", 1000, bar, self.previous, 100000, SIMULATION)
        self.assertEqual(fill.quantity, 300)
        self.assertEqual(fill.reason, "PARTIAL")
        fill = simulate_fill("600001", "BUY", 1000, self.bar, self.previous, 999, SIMULATION)
        self.assertEqual(fill.quantity, 0)

    def test_suspension_limits_and_slippage_do_not_create_fill(self):
        for bar, reason in (
            (None, "MISSING_BAR"),
            (replace(self.bar, volume=0), "SUSPENDED"),
            (replace(self.bar, open=11, high=11.1, low=10.9, close=11), "PRICE_LIMIT"),
        ):
            self.assertEqual(simulate_fill("600001", "BUY", 100, bar, self.previous, 100000, SIMULATION).reason, reason)
        fill = simulate_fill("600001", "BUY", 100, self.bar, self.previous, 100000,
                             replace(SIMULATION, slippage_rate=0.05))
        self.assertEqual(fill.reason, "SLIPPAGE_OUTSIDE_BAR")

    def test_minimum_commission_is_afforded_before_buy(self):
        config = replace(SIMULATION, min_commission=5)
        self.assertEqual(simulate_fill("600001", "BUY", 100, self.bar, self.previous, 1000, config).quantity, 0)
        fill = simulate_fill("600001", "BUY", 100, self.bar, self.previous, 1005, config)
        self.assertEqual((fill.quantity, fill.fee), (100, 5))


class ResearchEngineTest(unittest.TestCase):
    def test_weights_obey_limits_and_ignore_future_rows(self):
        dataset = dataset_fixture()
        day = dataset.bars["000300"][40].trade_date
        truncated = {symbol: rows[:41] for symbol, rows in dataset.bars.items()}
        full_weights = target_weights(dataset.bars, ("600001", "600002"), day, STRATEGY)
        self.assertEqual(full_weights, target_weights(truncated, ("600001", "600002"), day, STRATEGY))
        self.assertLessEqual(sum(full_weights.values()), STRATEGY.gross_limit)
        self.assertTrue(all(0 <= weight <= STRATEGY.max_weight for weight in full_weights.values()))

    def test_adding_future_data_does_not_change_past_ledger(self):
        dataset = dataset_fixture()
        dates = [bar.trade_date for bar in dataset.bars["000300"]]
        short = simulate_portfolio(dataset, STRATEGY, SIMULATION, start=dates[10], end=dates[50])
        changed_bars = {
            symbol: [replace(bar, open=bar.open * 2, close=bar.close * 2, high=bar.high * 2, low=bar.low * 2)
                     if bar.trade_date > dates[50] else bar for bar in rows]
            for symbol, rows in dataset.bars.items()
        }
        long = simulate_portfolio(replace(dataset, bars=changed_bars), STRATEGY, SIMULATION, start=dates[10], end=dates[90])
        self.assertEqual(short["equity_curve"], long["equity_curve"][:41])
        self.assertEqual(short["orders"], [order for order in long["orders"] if order["trade_date"] <= dates[50].isoformat()])
        self.assertTrue(all(order["signal_date"] < order["trade_date"] for order in long["orders"]))

    def test_cash_ledger_reconciles_and_no_terminal_fake_sale(self):
        dataset = dataset_fixture()
        dates = [bar.trade_date for bar in dataset.bars["000300"]]
        result = simulate_portfolio(dataset, STRATEGY, replace(SIMULATION, min_commission=5),
                                    start=dates[10], end=dates[50])
        cash = SIMULATION.initial_cash
        for order in result["orders"]:
            amount = order["price"] * order["filled_quantity"]
            cash += (-amount if order["side"] == "BUY" else amount) - order["fee"]
            self.assertAlmostEqual(cash, order["cash_after"], places=2)
            self.assertGreaterEqual(order["cash_after"], 0)
        self.assertAlmostEqual(cash, result["equity_curve"][-1]["cash"], places=2)
        self.assertGreater(result["summary"]["open_positions"], 0)
        self.assertAlmostEqual(result["summary"]["final_equity"],
                               cash + result["equity_curve"][-1]["market_value"], places=2)

    def test_drawdown_halt_retries_blocked_exit_and_never_reopens(self):
        dataset = dataset_fixture(trend=0)
        dates = [bar.trade_date for bar in dataset.bars["000300"]]
        rows = dataset.bars["600001"]
        for index in range(11, len(rows)):
            price = 9 if index == 11 else 8.1
            rows[index] = replace(rows[index], open=price, close=price, low=price - 0.1, high=price + 0.1)
        with patch("research.engine.target_weights", return_value={"600001": 0.4}):
            result = simulate_portfolio(dataset, STRATEGY, replace(SIMULATION, max_drawdown=0.03),
                                        start=dates[10], end=dates[20])
        self.assertTrue(result["summary"]["halted"])
        self.assertEqual(result["summary"]["open_positions"], 0)
        self.assertTrue(any(order["side"] == "SELL" and order["reason"] == "PRICE_LIMIT" for order in result["orders"]))
        self.assertEqual(sum(order["side"] == "BUY" for order in result["orders"]), 1)
        self.assertGreater(result["summary"]["max_drawdown"], 0.03)

    def test_missing_marks_trigger_halt_without_future_backfill(self):
        dataset = dataset_fixture(trend=0)
        dates = [bar.trade_date for bar in dataset.bars["000300"]]
        dataset.bars["600001"] = [bar for bar in dataset.bars["600001"] if bar.trade_date not in dates[12:16]]
        with patch("research.engine.target_weights", return_value={"600001": 0.4}):
            result = simulate_portfolio(dataset, STRATEGY, replace(SIMULATION, max_stale_days=1),
                                        start=dates[10], end=dates[20])
        self.assertTrue(any(item["kind"] == "STALE_DATA_HALT" for item in result["risk_events"]))
        self.assertEqual(result["equity_curve"][2]["market_value"], 40000)
        self.assertEqual(result["equity_curve"][2]["stale_symbols"], ["600001"])

    def test_failed_sale_does_not_release_position_slot(self):
        dataset = dataset_fixture(trend=0)
        dates = [bar.trade_date for bar in dataset.bars["000300"]]
        dataset.bars["600001"][11] = replace(dataset.bars["600001"][11], volume=0)
        with patch("research.engine.target_weights", side_effect=[{"600001": 0.4}, {"600002": 0.4}]):
            result = simulate_portfolio(dataset, replace(STRATEGY, max_positions=1), SIMULATION,
                                        start=dates[10], end=dates[11])
        self.assertEqual(result["terminal_positions"], {"600001": 4000})
        self.assertTrue(any(order["reason"] == "POSITION_COUNT_LIMIT" for order in result["orders"]))

    def test_membership_removal_sells_only_after_it_is_known(self):
        dataset = dataset_fixture(trend=0)
        dates = [bar.trade_date for bar in dataset.bars["000300"]]
        dataset = replace(dataset, universe=dataset.universe + (UniverseSnapshot(dates[11], dates[11], ()),))
        with patch("research.engine.target_weights", return_value={"600001": 0.4}):
            result = simulate_portfolio(dataset, STRATEGY, SIMULATION, start=dates[10], end=dates[12])
        self.assertFalse(any(order["side"] == "SELL" and order["trade_date"] == dates[11].isoformat() for order in result["orders"]))
        self.assertEqual(result["terminal_positions"], {})


class ResearchExperimentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = dataset_fixture()
        cls.validation = ValidationConfig(train_days=60, test_days=20, bootstrap_samples=100)
        cls.report = run_experiment(cls.dataset, STRATEGY, SIMULATION, cls.validation)

    def test_oos_dates_disjoint_and_state_continuous(self):
        report = self.report
        for left, right in zip(report["folds"], report["folds"][1:]):
            self.assertLess(left["test_end"], right["test_start"])
        for fold in report["folds"]:
            self.assertLess(fold["train_end"], fold["test_start"])
        self.assertEqual(len(report["out_of_sample"]["equity_curve"]),
                         sum(fold["oos"]["days"] for fold in report["folds"]))
        self.assertFalse(report["live_trading_enabled"])
        self.assertEqual(report["release_status"], "BLOCKED_SYNTHETIC")
        self.assertEqual(report["cost_stress"]["decisions"][0]["signal_date"],
                         report["out_of_sample"]["decisions"][0]["signal_date"])

    def test_future_test_outcome_cannot_select_first_fold_parameter(self):
        dataset = copy.deepcopy(self.dataset)
        first_test = self.report["folds"][0]["test_start"]
        for symbol, rows in dataset.bars.items():
            dataset.bars[symbol] = [
                replace(bar, open=bar.open * 0.8, close=bar.close * 0.8, high=bar.high * 0.8, low=bar.low * 0.8)
                if bar.trade_date.isoformat() >= first_test else bar for bar in rows
            ]
        changed = run_experiment(dataset, STRATEGY, SIMULATION, self.validation)
        self.assertEqual(self.report["folds"][0]["candidates"], changed["folds"][0]["candidates"])
        self.assertEqual(self.report["folds"][0]["selected_lookback"], changed["folds"][0]["selected_lookback"])

    def test_bootstrap_is_deterministic_and_small_samples_unknown(self):
        self.assertEqual(block_bootstrap_interval([0.01] * 50, 100), block_bootstrap_interval([0.01] * 50, 100))
        self.assertIsNone(block_bootstrap_interval([0.01], 100)["low"])

    def test_saved_runs_reproduce_and_detect_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = save_experiment(self.report, self.dataset, temporary)
            with patch.dict("os.environ", {"QUANT_EXPERIMENT_DIR": temporary}):
                loaded = load_experiment(path.name)
                self.assertEqual(loaded["data_hash"], self.dataset.fingerprint())
                self.assertEqual(len(list_experiments()), 1)
                recreated = run_experiment(load_dataset(path / "dataset.json"),
                                          *parse_config(json.loads((path / "config.json").read_text())))
                self.assertEqual(recreated, self.report)
                (path / "report.md").write_text("被修改的报告", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "校验失败"):
                    load_experiment(path.name)
                with self.assertRaises(ValueError):
                    load_experiment("../report")

    def test_cli_demo_and_http_service_stay_offline(self):
        with tempfile.TemporaryDirectory() as temporary, patch("urllib.request.urlopen", side_effect=AssertionError("禁止网络")):
            with redirect_stdout(io.StringIO()) as stream:
                exit_code = main(["research", "--demo", "--output", temporary, "--format", "json"])
            payload = json.loads(stream.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertFalse(payload["live_trading_enabled"])
            with patch.dict("os.environ", {"QUANT_EXPERIMENT_DIR": temporary}):
                result = build_experiment_response({"demo": True})
            self.assertEqual(result["experiment"]["release_status"], "BLOCKED_SYNTHETIC")
            for payload in ({"demo": "true"}, {"demo": True, "dataset": {}}, {}, {"demo": True, "path": "/tmp"}):
                with self.assertRaises(ValueError):
                    build_experiment_response(payload)

    def test_demo_fingerprint_is_deterministic(self):
        self.assertEqual(build_demo_dataset().fingerprint(), build_demo_dataset().fingerprint())


if __name__ == "__main__":
    unittest.main()
