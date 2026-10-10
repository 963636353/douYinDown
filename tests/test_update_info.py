"""更新文件接口与客户端解析。使用临时文件，不修改正式更新配置。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from core.update_checker import APP_VERSION, UPDATE_CHECK_URL, fetch_update_info, has_new_version

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "服务端"))
from payment_server.app import create_app
from payment_server.config import Settings


class UpdateInfoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / "videoDown.txt"
        self.data = {"version_number": "20260614", "百度": "https://example.com/baidu",
                     "夸克": "https://example.com/quark", "info": "更新说明\n1. 修复下载问题", "forced_upgrade": "False"}
        self.write(self.data)
        directory = patch("payment_server.app.SERVER_DIR", self.folder)
        directory.start()
        self.addCleanup(directory.stop)
        app = create_app(Settings(mode="demo", database_url=f"sqlite:///{self.folder.as_posix()}/test.db"))
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def write(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def bridge(self, url, **kwargs):
        result = self.client.get("/videoDown.txt")
        response = requests.Response()
        response.status_code, response._content, response.url = result.status_code, result.content, url
        response.encoding = "utf-8"
        return response

    def test_public_json_retains_fields_unicode_and_newlines_without_registration(self):
        response = self.client.get("/videoDown.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.data)
        self.assertIn("更新说明", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.assertEqual(self.client.get("/api/pc/stats").json()["total_users"], 0)

    def test_saved_changes_are_visible_without_recreating_app(self):
        self.assertEqual(self.client.get("/videoDown.txt").json(), self.data)
        changed = dict(self.data, version_number="20261009", info="第二版说明", forced_upgrade=True)
        self.write(changed)
        self.assertEqual(self.client.get("/videoDown.txt").json(), changed)

    def test_missing_and_invalid_file_return_uncached_503(self):
        self.path.unlink()
        self.assertEqual(self.client.get("/videoDown.txt").status_code, 503)
        for content in (b"{", b"[]", b"null", b"\xff", b"{}"):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                response = self.client.get("/videoDown.txt")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertIn("更新信息文件缺失或格式不正确", response.json()["detail"])
                self.assertNotIn("version_number", response.json())

    def test_fixed_file_does_not_expose_other_files_or_extra_fields(self):
        (self.folder / ".env").write_text("TEST_SECRET=private-test-value", encoding="utf-8")
        self.write(dict(self.data, private_setting="private-test-value"))
        self.assertEqual(self.client.get("/videoDown.txt?path=.env").json(), self.data)
        for path in ("/.env", "/payment_server/config.py", "/videoDown.txt/.env"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_client_uses_new_url_and_retains_all_parsed_values(self):
        with patch("core.update_checker.requests.get", side_effect=self.bridge) as get:
            result = fetch_update_info()
        self.assertEqual(UPDATE_CHECK_URL, "https://douyindown.secs.cc/videoDown.txt")
        get.assert_called_once_with(UPDATE_CHECK_URL, timeout=5,
                                    headers={"User-Agent": f"QingningVideoDownloader/{APP_VERSION}"})
        self.assertEqual((result.version_number, result.baidu_url, result.quark_url, result.info, result.forced_upgrade),
                         (self.data["version_number"], self.data["百度"], self.data["夸克"], self.data["info"], False))

    def test_client_accepts_boolean_and_original_string_forced_upgrade(self):
        for value, expected in ((False, False), (True, True), ("False", False), ("True", True)):
            with self.subTest(value=value), patch("core.update_checker.requests.get", side_effect=self.bridge):
                self.write(dict(self.data, forced_upgrade=value))
                self.assertEqual(fetch_update_info().forced_upgrade, expected)

    def test_missing_file_still_causes_client_request_failure(self):
        self.path.unlink()
        with patch("core.update_checker.requests.get", side_effect=self.bridge):
            with self.assertRaises(requests.HTTPError):
                fetch_update_info()

    def test_version_comparison_retains_existing_behavior(self):
        with patch("core.update_checker.requests.get", side_effect=self.bridge):
            self.write(dict(self.data, version_number=APP_VERSION))
            self.assertFalse(has_new_version(fetch_update_info()))
            self.write(dict(self.data, version_number="20261009"))
            self.assertTrue(has_new_version(fetch_update_info()))


if __name__ == "__main__":
    unittest.main()
