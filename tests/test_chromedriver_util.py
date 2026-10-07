import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from core import browser_login, chromedriver_util


def json_response(data):
    return io.BytesIO(json.dumps(data).encode('utf-8'))


class ChromeDriverVersionTest(unittest.TestCase):
    def test_matches_available_patch_instead_of_browser_patch(self):
        with patch('core.chromedriver_util.urllib.request.urlopen', return_value=json_response({
            'builds': {'154.0.8037': {'version': '154.0.8037.92'}},
        })) as fetch:
            version = chromedriver_util._resolve_chromedriver_version('154.0.8037.98')
        self.assertEqual('154.0.8037.92', version)
        self.assertEqual(1, fetch.call_count)

    def test_missing_build_falls_back_to_same_major_version(self):
        with patch('core.chromedriver_util.urllib.request.urlopen', side_effect=[
            json_response({'builds': {}}),
            json_response({'milestones': {'154': {'version': '154.0.8037.92'}}}),
        ]):
            self.assertEqual('154.0.8037.92', chromedriver_util._resolve_chromedriver_version('154.0.8000.1'))

    def test_download_uses_matched_patch_and_extracts_driver(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('chromedriver-win32/chromedriver.exe', b'test-driver')
        with tempfile.TemporaryDirectory() as cache:
            with patch('core.chromedriver_util.urllib.request.urlopen', side_effect=[
                json_response({'builds': {'154.0.8037': {'version': '154.0.8037.92'}}}),
                io.BytesIO(archive.getvalue()),
            ]) as fetch:
                driver = chromedriver_util._download_chromedriver('154.0.8037.98', cache)
            self.assertEqual(b'test-driver', Path(driver).read_bytes())
            self.assertIn('/154.0.8037.92/', fetch.call_args.args[0].full_url)

    def test_undetected_fallback_requests_installed_major_instead_of_latest(self):
        with (
            patch.object(browser_login, 'HAS_SELENIUM', True),
            patch.object(browser_login, 'find_chrome_executable', return_value='chrome.exe'),
            patch.object(browser_login, 'ensure_chromedriver', return_value=None),
            patch.object(browser_login, '_get_chrome_version', return_value='154.0.8037.98'),
            patch.object(browser_login.uc, 'ChromeOptions'),
            patch.object(browser_login.uc, 'Chrome') as launch,
            patch.object(browser_login, '_run_selenium_login', return_value={}),
        ):
            browser_login._login_undetected(None, None)
        self.assertEqual(154, launch.call_args.kwargs['version_main'])


if __name__ == '__main__':
    unittest.main()
