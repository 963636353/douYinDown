import unittest
from unittest.mock import Mock, patch

import requests

from platforms.douyin.platform import DouyinPlatform


VIDEO_ID = '7679721287379341747'
BROWSER_URL = f'https://www.douyin.com/jingxuan?modal_id={VIDEO_ID}'
SHORT_URL = 'https://v.douyin.com/U2h6qkqtspI/'


def response(status, data=None):
    result = Mock()
    result.status_code = status
    result.text = ''
    result.json.return_value = data
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(response=result)
    return result


def detail_response():
    return response(200, {'status_code': 0, 'aweme_detail': {
        'aweme_id': VIDEO_ID, 'desc': 'test video',
        'video': {'play_addr': {'url_list': ['https://example.com/test.mp4']}}}})


class VideoDetailTests(unittest.TestCase):
    def setUp(self):
        self.platform = DouyinPlatform()

    def test_browser_modal_link_extracts_id_without_requesting_page(self):
        with patch('platforms.douyin.url_parser.requests.get') as get:
            self.assertEqual(self.platform.extract_video_id(BROWSER_URL), VIDEO_ID)
        get.assert_not_called()

    def test_browser_and_share_link_both_recover_a_temporary_403(self):
        for url in (BROWSER_URL, SHORT_URL):
            with self.subTest(url=url):
                redirects = []
                if url == SHORT_URL:
                    redirected = response(200)
                    redirected.url = f'https://www.douyin.com/video/{VIDEO_ID}'
                    redirects.append(redirected)
                with patch('platforms.douyin.platform.requests.get',
                           side_effect=redirects + [response(403), detail_response()]) as get, \
                        patch('platforms.douyin.api.generate_abogus', side_effect=['fresh-one', 'fresh-two']) as sign, \
                        patch('platforms.douyin.platform.time.sleep') as wait:
                    video_id = self.platform.extract_video_id(url)
                    info = self.platform.get_video_info(video_id, {'sessionid': 'test-only'})
                self.assertEqual(info.video_id, VIDEO_ID)
                self.assertEqual(info.video_url, 'https://example.com/test.mp4')
                self.assertEqual(sign.call_count, 2)
                self.assertTrue(all(f'aweme_id={VIDEO_ID}' in call.args[0] for call in sign.call_args_list))
                self.assertEqual(get.call_count, 2 + len(redirects))
                wait.assert_called_once_with(0.5)

    def test_persistent_403_is_bounded_and_does_not_report_bad_link(self):
        with patch('platforms.douyin.platform.requests.get', side_effect=[response(403) for _ in range(3)]) as get, \
                patch('platforms.douyin.api.generate_abogus', return_value='test-only'), \
                patch('platforms.douyin.platform.time.sleep') as wait:
            with self.assertRaisesRegex(RuntimeError, '链接已识别.*403'):
                self.platform.get_video_info(VIDEO_ID, {})
        self.assertEqual(get.call_count, 3)
        self.assertEqual([call.args[0] for call in wait.call_args_list], [0.5, 1.0])

    def test_argus_identity_rejection_does_not_blindly_retry(self):
        blocked = response(403)
        blocked.text = 'Blocked by ArgusSecurityPlugin Uifid Not Found'
        with patch('platforms.douyin.platform.requests.get', return_value=blocked) as get, \
                patch('platforms.douyin.api.generate_abogus', return_value='test-only'), \
                patch('platforms.douyin.platform.time.sleep') as wait:
            with self.assertRaisesRegex(RuntimeError, '身份信息或签名'):
                self.platform.get_video_info(VIDEO_ID, {})
        get.assert_called_once()
        wait.assert_not_called()

    def test_other_http_errors_and_unavailable_video_are_not_retried(self):
        with patch('platforms.douyin.platform.requests.get', return_value=response(404)) as get, \
                patch('platforms.douyin.api.generate_abogus', return_value='test-only'), \
                patch('platforms.douyin.platform.time.sleep') as wait:
            with self.assertRaises(requests.HTTPError):
                self.platform.get_video_info(VIDEO_ID, {})
        get.assert_called_once()
        wait.assert_not_called()
        unavailable = response(200, {'status_code': 0, 'filter_detail': {'filter_reason': 'removed'}})
        with patch('platforms.douyin.platform.requests.get', return_value=unavailable) as get, \
                patch('platforms.douyin.api.generate_abogus', return_value='test-only'):
            with self.assertRaisesRegex(Exception, 'Video unavailable: removed'):
                self.platform.get_video_info(VIDEO_ID, {})
        get.assert_called_once()


if __name__ == '__main__':
    unittest.main()
