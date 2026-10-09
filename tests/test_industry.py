import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data.errors import DataRefreshError
from data.industry import IndustryDirectory, _fetch_sector_members, _fetch_sectors


class IndustryScenarios(unittest.TestCase):
    def test_sector_wrapper_is_parsed_as_data_not_executed(self):
        """Given 公开行业赋值响应 When 解析 Then 仅解析JSON并保留来源的行业名"""
        text = 'var S_Finance_bankuai_industry = {"hangye_ZJ66":"hangye_ZJ66,货币金融服务,44,0"};'
        with patch("data.industry._fetch_text", return_value=text):
            self.assertEqual(_fetch_sectors(), [("hangye_ZJ66", "货币金融服务")])
        with patch("data.industry._fetch_text", return_value="alert('unexpected')"):
            with self.assertRaises(DataRefreshError):
                _fetch_sectors()

    def test_members_paginate_and_do_not_treat_sector_summary_count_as_total(self):
        """Given 行业首批达到一百只 When 读取成分 Then 继续分页并保留全部股票代码"""
        rows = [{"code": f"600{index:03d}"} for index in range(100)]
        with patch("data.industry._fetch_json", side_effect=[rows, [{"code": "601000"}]]) as fetch:
            members = _fetch_sector_members("hangye_ZJ66")
        self.assertEqual(len(members), 101)
        self.assertIn("page=2", fetch.call_args.args[0])

    def test_bulk_directory_caches_public_metadata_and_unknown_is_explicit(self):
        """Given 完整公开行业列表 When 缓存并重载 Then 不逐股查询且未知行业不猜测"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "industries.json"
            catalog = IndustryDirectory(path=path)
            with patch("data.industry._fetch_sectors", return_value=[("hangye_ZJ66", "货币金融服务")]), \
                 patch("data.industry._fetch_sector_members", return_value=["600001"]) as loader:
                catalog.refresh()
                catalog._thread.join(3)
                catalog.refresh()
            self.assertEqual(loader.call_count, 1)
            self.assertEqual(catalog.lookup("600001")["industry"], "货币金融服务")
            self.assertEqual(catalog.lookup("600009")["industry"], "")
            self.assertEqual(catalog.lookup("600001")["industry_source"], "新浪行业分类")
            restored = IndustryDirectory(path=path)
            self.assertEqual(restored.lookup("600001")["industry"], "货币金融服务")
            self.assertNotIn("positions", json.loads(path.read_text()))

    def test_conflicting_classifications_are_not_arbitrarily_selected(self):
        """Given 同一股票在两个不同行业中 When 合并 Then 标记分类冲突而非随意取一个"""
        with tempfile.TemporaryDirectory() as directory:
            catalog = IndustryDirectory(path=Path(directory) / "industries.json")
            with patch("data.industry._fetch_sectors", return_value=[("hangye_ZJ66", "甲"), ("hangye_ZJ67", "乙")]), \
                 patch("data.industry._fetch_sector_members", return_value=["600001"]):
                catalog.refresh()
                catalog._thread.join(3)
            self.assertEqual(catalog.lookup("600001")["industry"], "")
            self.assertEqual(catalog.lookup("600001")["industry_state"], "CONFLICT")


if __name__ == "__main__":
    unittest.main()
