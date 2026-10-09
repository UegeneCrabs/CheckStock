import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import Settings
from app.wb.storefront_browser import _browser_command, _wait_for_browser_start, browser_path


class BrowserPathTests(unittest.TestCase):
    def test_uses_playwright_chromium_without_yandex_browser(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            chromium = Path(directory) / "chrome.exe"
            chromium.touch()
            playwright = mock.MagicMock()
            playwright.chromium.executable_path = str(chromium)
            context = mock.MagicMock()
            context.__enter__.return_value = playwright
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "CHECKSTOCK_WB_BROWSER_PATH": "",
                        "LOCALAPPDATA": directory,
                        "PROGRAMFILES": directory,
                        "PROGRAMFILES(X86)": directory,
                    },
                ),
                mock.patch("playwright.sync_api.sync_playwright", return_value=context),
            ):
                self.assertEqual(browser_path(None), chromium.resolve())

    def test_explicit_path_takes_priority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "custom-browser.exe"
            executable.touch()
            self.assertEqual(browser_path(executable), executable.resolve())

    def test_uses_yandex_when_playwright_chromium_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            yandex = Path(directory) / "Yandex/YandexBrowser/Application/browser.exe"
            yandex.parent.mkdir(parents=True)
            yandex.touch()
            playwright = mock.MagicMock()
            playwright.chromium.executable_path = str(Path(directory) / "missing-chrome.exe")
            context = mock.MagicMock()
            context.__enter__.return_value = playwright
            with (
                mock.patch.dict(os.environ, {"CHECKSTOCK_WB_BROWSER_PATH": "", "LOCALAPPDATA": directory}),
                mock.patch("playwright.sync_api.sync_playwright", return_value=context),
            ):
                self.assertEqual(browser_path(None), yandex.resolve())


class BrowserCommandTests(unittest.TestCase):
    def test_visible_mode_can_be_enabled_from_environment(self) -> None:
        with mock.patch.dict(os.environ, {"CHECKSTOCK_WB_BROWSER_HEADLESS": "0"}):
            self.assertFalse(Settings.from_env().wb_storefront_browser_headless)

    def test_headless_mode_can_be_switched(self) -> None:
        executable = Path("chrome.exe")
        profile = Path("profile")
        hidden = _browser_command(executable, profile, 9222, "153985484", headless=True)
        visible = _browser_command(executable, profile, 9222, "153985484", headless=False)
        self.assertIn("--headless=new", hidden)
        self.assertNotIn("--headless=new", visible)
        self.assertEqual(hidden[-1], visible[-1])

    def test_no_sandbox_requires_explicit_setting(self) -> None:
        with mock.patch.dict(os.environ, {"CHECKSTOCK_WB_BROWSER_NO_SANDBOX": "0"}):
            self.assertFalse(Settings.from_env().wb_storefront_browser_no_sandbox)
        with mock.patch.dict(os.environ, {"CHECKSTOCK_WB_BROWSER_NO_SANDBOX": "1"}):
            self.assertTrue(Settings.from_env().wb_storefront_browser_no_sandbox)

        executable = Path("chrome.exe")
        profile = Path("profile")
        default = _browser_command(executable, profile, 9222, "153985484", headless=True)
        opt_in = _browser_command(executable, profile, 9222, "153985484", headless=True, no_sandbox=True)
        self.assertNotIn("--no-sandbox", default)
        self.assertIn("--no-sandbox", opt_in)

    def test_browser_exit_is_reported_before_cdp_connection(self) -> None:
        process = mock.Mock()
        process.wait.return_value = 2147483651
        with self.assertRaisesRegex(RuntimeError, "код 2147483651"):
            _wait_for_browser_start(process)
        process.wait.assert_called_once_with(timeout=25)

    def test_running_browser_reaches_cdp_connection(self) -> None:
        process = mock.Mock()
        process.wait.side_effect = subprocess.TimeoutExpired("chrome.exe", 25)
        _wait_for_browser_start(process)
