from __future__ import annotations

import unittest
from datetime import date

from data.market_rules import is_limit_price, price_limit_rate, price_limit_value


class MarketRulesTest(unittest.TestCase):
    def test_main_board_uses_ten_percent_limit(self) -> None:
        self.assertEqual(price_limit_rate("600519", "贵州茅台", date(2026, 7, 29)), 0.10)
        self.assertTrue(is_limit_price(11.0, 10.0, "600519", "贵州茅台", date(2026, 7, 29), "UP"))

    def test_growth_and_star_boards_use_twenty_percent_limit(self) -> None:
        self.assertEqual(price_limit_rate("300001", "测试", date(2026, 7, 29)), 0.20)
        self.assertEqual(price_limit_rate("688001", "测试", date(2026, 7, 29)), 0.20)

    def test_beijing_board_uses_thirty_percent_limit(self) -> None:
        self.assertEqual(price_limit_rate("920001", "测试", date(2026, 7, 29)), 0.30)

    def test_old_risk_warning_bar_uses_five_percent_limit(self) -> None:
        self.assertEqual(price_limit_rate("600001", "*ST测试", date(2026, 7, 3)), 0.05)
        self.assertEqual(price_limit_rate("600001", "*ST测试", date(2026, 7, 6)), 0.10)

    def test_limit_price_rounds_to_cent(self) -> None:
        self.assertEqual(price_limit_value(10.03, 0.10, "UP"), 11.03)
        self.assertEqual(price_limit_value(10.03, 0.10, "DOWN"), 9.03)


if __name__ == "__main__":
    unittest.main()
