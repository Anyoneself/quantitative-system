"""系统设置页面：同花顺客户端下载链接的静态验收。"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit

WEB_STATIC = Path(__file__).resolve().parents[1] / "src" / "web_static"
OFFICIAL_HOST = "download.10jqka.com.cn"


def page_section(markup: str, name: str) -> str:
    """取出 data-page-panel="<name>" 的页面区块，避免匹配到其它页面的链接。"""
    for chunk in markup.split('<section class="page"'):
        if f'data-page-panel="{name}"' in chunk:
            return chunk
    raise AssertionError(f"未找到页面区块 {name}")


class SettingsPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (WEB_STATIC / "index.html").read_text(encoding="utf-8")
        cls.app_js = (WEB_STATIC / "app.js").read_text(encoding="utf-8")

    def test_navigation_opens_the_settings_page(self) -> None:
        # Given 系统设置此前只是 planned 占位按钮
        # When 用户点击侧边栏的系统设置
        # Then 按钮指向真实页面面板，且导航允许跳转到该页面
        self.assertNotIn('data-feature="系统设置"', self.html)
        self.assertIn('data-page="settings"', self.html)
        self.assertIn('data-page-panel="settings"', self.html)
        allowed = re.search(r"\[([^\]]*)\]\.includes\(page\)", self.app_js)
        self.assertIsNotNone(allowed, "navigateTo 应保留页面白名单校验")
        self.assertIn('"settings"', allowed.group(1))
        self.assertEqual(self.html.count('id="settings-page"'), 1)
        self.assertIn('page === "client" ? "settings"', self.app_js)

    def test_settings_page_links_to_official_client_downloads(self) -> None:
        # Given 使用只读连接需要先在本机安装同花顺客户端
        # When 用户打开系统设置
        # Then 页面同时提供 macOS 与 Windows 的官方下载链接，且只指向官方下载中心
        section = page_section(self.html, "settings")
        links = re.findall(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>', section)
        self.assertEqual({urlsplit(url).netloc for url in links}, {OFFICIAL_HOST})
        self.assertTrue(any("/free/mac" in url for url in links), "缺少 macOS 下载链接")
        self.assertTrue(any("/free/ths" in url for url in links), "缺少 Windows 下载链接")
        self.assertIn("macOS", section)
        self.assertIn("Windows", section)

    def test_official_links_open_safely_in_a_new_tab(self) -> None:
        # Given 下载链接指向站外域名
        # When 页面渲染这些链接
        # Then 每个链接都带上新的浏览上下文隔离，避免反向控制当前页面
        for anchor in re.findall(r"<a\b[^>]*>", page_section(self.html, "settings")):
            self.assertIn('target="_blank"', anchor)
            self.assertIn("noopener", anchor)

    def test_settings_page_states_the_read_only_bridge_limit(self) -> None:
        # Given 只读连接当前只在 macOS 的「应用程序/同花顺.app」上实现
        # When 用户看到 Windows 下载链接
        # Then 页面明确说明 Windows 客户端尚不能被识别，也不代为安装
        section = page_section(self.html, "settings")
        self.assertIn("尚不能", section)
        self.assertIn("官方", section)

    def test_settings_page_does_not_freeze_a_verification_date(self) -> None:
        # Given 官方下载地址会随客户端改版变化
        # When 页面标注链接来源
        # Then 不写死核对日期，避免页面内容随时间失真
        self.assertIsNone(re.search(r"20\d{2}[-/年]\d{1,2}", page_section(self.html, "settings")))


if __name__ == "__main__":
    unittest.main()
