import tempfile
import time
import unittest
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from unittest.mock import patch

from core import cookie_manager


class CookieExpiryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.cookie_dir = Path(self.temp_dir.name) / 'cookies'
        self.cookie_dir.mkdir()
        self.cookie_patch = patch.object(cookie_manager, 'COOKIE_DIR', str(self.cookie_dir))
        self.cookie_patch.start()
        self.addCleanup(self.cookie_patch.stop)

    def write_cookies(self, specs, *, legacy=False):
        cookie_path = (Path(self.temp_dir.name) if legacy else self.cookie_dir) / 'douyin_cookies.txt'
        jar = MozillaCookieJar(str(cookie_path))
        for name, expires in specs:
            cookie = cookie_manager._make_cookie(
                {'name': name, 'value': 'test-value', 'expiry': expires}, 'douyin', time.time(),
            )
            if expires is None:
                cookie.expires = None
                cookie.discard = True
            jar.set_cookie(cookie)
        jar.save(ignore_expires=True, ignore_discard=True)

    def test_expired_login_cookies_are_excluded_even_when_other_cookies_are_valid(self):
        now = int(time.time())
        self.write_cookies([
            ('sessionid', now - 86400), ('sid_tt', now - 86400), ('ttwid', now + 86400),
        ])
        self.assertEqual({'ttwid': 'test-value'}, cookie_manager.load_cookies('douyin'))

    def test_valid_and_session_cookies_are_kept(self):
        self.write_cookies([('sessionid', int(time.time()) + 86400), ('sid_tt', None)])
        self.assertEqual(
            {'sessionid': 'test-value', 'sid_tt': 'test-value'},
            cookie_manager.load_cookies('douyin'),
        )

    def test_legacy_cookie_file_also_excludes_expired_cookies(self):
        self.write_cookies([('sessionid', int(time.time()) - 86400)], legacy=True)
        self.assertEqual({}, cookie_manager.load_cookies('douyin'))


if __name__ == '__main__':
    unittest.main()
