import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from platforms.douyin import api
from platforms.douyin.platform import DouyinPlatform


class SigningTests(unittest.TestCase):
    def test_web_signature_matches_fixed_vector_and_wire_query(self):
        with patch.object(api, 'generate_abogus', return_value='aweme_id=123&a_bogus=a/b%2Bc='), \
                patch.object(api.time, 'time', return_value=1700000000):
            query, headers = api.build_signed_request({'aweme_id': '123'}, {'UIFID': 'visitor'})
        expected = ('aweme_id=123&a_bogus=a%2Fb%2Bc%3D&uifid=visitor&timestamp=1700000000'
                    '&x-secsdk-web-signature=47863e288590cff4c33440ae2ea6b08a')
        self.assertEqual(query, expected)
        self.assertEqual(headers['x-secsdk-web-signature'], '47863e288590cff4c33440ae2ea6b08a')
        self.assertEqual(headers['x-secsdk-web-expire'], '1700000000')
        self.assertEqual(headers['uifid'], 'visitor')

    def test_browser_identity_used_without_mutating_caller(self):
        original = {'aweme_id': '123', 'msToken': 'old'}
        cookies = {'UIFID_TEMP': 'visitor', 's_v_web_id': 'verify_same', 'msToken': 'real-token'}
        with patch.object(api, 'generate_abogus', side_effect=lambda query: query + '&a_bogus=test') as sign:
            query, headers = api.build_signed_request(original, cookies)
        signed = parse_qs(sign.call_args.args[0])
        self.assertEqual(signed['verifyFp'], ['verify_same'])
        self.assertEqual(signed['fp'], ['verify_same'])
        self.assertEqual(signed['msToken'], ['real-token'])
        self.assertEqual(parse_qs(query)['uifid'], ['visitor'])
        self.assertEqual(original, {'aweme_id': '123', 'msToken': 'old'})
        self.assertEqual(headers['uifid'], 'visitor')

    def test_missing_identity_is_not_fabricated_and_cdn_headers_are_unchanged(self):
        with patch.object(api, 'generate_abogus', return_value='aweme_id=123&a_bogus=test'):
            query, headers = api.build_signed_request({'aweme_id': '123'}, {'sessionid': 'test'})
        self.assertEqual(query, 'aweme_id=123&a_bogus=test')
        self.assertNotIn('uifid', headers)
        self.assertNotIn('x-secsdk-web-signature', headers)
        download_headers = DouyinPlatform().build_download_headers({'UIFID': 'visitor'})
        self.assertNotIn('uifid', download_headers)
        self.assertNotIn('x-secsdk-web-signature', download_headers)

    def test_encoding_matches_url_search_params(self):
        self.assertEqual(api._encode_query([('text', "中文 ~!*'()/+"), ('empty', '')]),
                         'text=%E4%B8%AD%E6%96%87+%7E%21*%27%28%29%2F%2B&empty=')

    def test_existing_identity_is_not_duplicated(self):
        with patch.object(api, 'generate_abogus', return_value='uifid=visitor&aweme_id=123&a_bogus=test'):
            query, _ = api.build_signed_request({'aweme_id': '123'}, {'UIFID': 'visitor'})
        self.assertEqual(parse_qs(query)['uifid'], ['visitor'])


if __name__ == '__main__':
    unittest.main()
