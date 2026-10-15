"""Feedback protocol and preparation tests; no production requests or email."""

import json
import tempfile
import threading
import unittest
from dataclasses import asdict, replace
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from core.feedback import FeedbackDraft, FeedbackError, FeedbackLimits, FeedbackReceipt, FeedbackService
from core.feedback.client import FeedbackClient
from core.feedback.diagnostics import DiagnosticCollector, sanitize_log
from core.feedback.models import FeedbackAttachment, REQUEST_MAX_BYTES


def response(status, data=None, *, headers=None, body=None):
    result = requests.Response()
    result.status_code = status
    result.encoding = 'utf-8'
    result._content = body if body is not None else json.dumps(data).encode('utf-8')
    result.headers.update(headers or {})
    return result


class FeedbackClientTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.client = FeedbackClient('https://feedback.example', http=self.http)
        self.fields = {'user_uuid': 'fixed-id', 'software_name': '测试软件', 'feedback_type': 'problem', 'content': '问题'}

    def test_config_parsing_and_no_redirects(self):
        self.http.get.return_value = response(200, asdict(FeedbackLimits()))
        limits = self.client.fetch_limits()
        self.assertEqual(limits.max_images, 4)
        self.assertLess(limits.image_budget, limits.max_total_bytes)
        self.assertFalse(self.http.get.call_args.kwargs['allow_redirects'])

    def test_invalid_config_rejected(self):
        for data in ([], {}, dict(asdict(FeedbackLimits()), max_files=True),
                     dict(asdict(FeedbackLimits()), extensions='png')):
            with self.subTest(data=data):
                self.http.get.return_value = response(200, data)
                with self.assertRaises(FeedbackError):
                    self.client.fetch_limits()

    def test_success_validates_receipt_and_uses_multipart_without_attachments(self):
        self.http.post.return_value = response(202, {'ok': True, 'feedback_id': 'a' * 32})
        receipt = self.client.submit(self.fields, ())
        self.assertEqual(receipt.feedback_id, 'a' * 32)
        options = self.http.post.call_args.kwargs
        self.assertIn('multipart/form-data; boundary=', options['headers']['Content-Type'])
        self.assertIn('测试软件'.encode(), options['data'])
        self.assertIn(b'name="user_uuid"', options['data'])
        self.assertNotIn(b'filename=', options['data'])
        self.assertFalse(options['allow_redirects'])

    def test_unknown_receipt_is_ambiguous_not_success(self):
        for status, data in ((202, {'ok': True}), (202, {'ok': True, 'feedback_id': 'invalid'}),
                             (200, {'ok': True, 'feedback_id': 'b' * 32})):
            self.http.post.return_value = response(status, data)
            with self.assertRaises(FeedbackError) as caught:
                self.client.submit(self.fields, ())
            if status == 202:
                self.assertTrue(caught.exception.result_unknown)

    def test_rate_limit_accepts_header_or_body_and_uses_longer_wait(self):
        self.http.post.return_value = response(429, {'retry_after': 91}, headers={'Retry-After': '42'})
        with self.assertRaises(FeedbackError) as caught:
            self.client.submit(self.fields, ())
        self.assertEqual(caught.exception.retry_after, 91)
        self.assertFalse(caught.exception.result_unknown)

    def test_html_errors_and_json_errors_are_user_readable(self):
        for status in (413, 422, 503, 502):
            with self.subTest(status=status):
                self.http.post.return_value = response(status, body=b'<html>gateway error</html>')
                with self.assertRaises(FeedbackError) as caught:
                    self.client.submit(self.fields, ())
                self.assertNotIn('<html>', str(caught.exception))
                self.assertEqual(caught.exception.result_unknown, status == 502)
        self.http.post.return_value = response(422, {'ok': False, 'message': '图片格式无效'})
        with self.assertRaisesRegex(FeedbackError, '图片格式无效'):
            self.client.submit(self.fields, ())

    def test_timeout_never_retries_or_exposes_request_details(self):
        self.http.post.side_effect = requests.Timeout('Authorization: secret-value')
        with self.assertRaises(FeedbackError) as caught:
            self.client.submit(self.fields, ())
        self.assertTrue(caught.exception.result_unknown)
        self.assertNotIn('secret-value', str(caught.exception))
        self.assertEqual(self.http.post.call_count, 1)

    def test_request_total_is_checked_before_transport(self):
        file = FeedbackAttachment('large.txt', b'x' * REQUEST_MAX_BYTES, 'text/plain')
        with self.assertRaisesRegex(FeedbackError, '请求大小'):
            self.client.submit(self.fields, (file,))
        self.http.post.assert_not_called()

    def test_production_requires_https_but_local_http_is_allowed(self):
        for address in ('http://example.com', 'https://user:password@example.com', 'https://example.com/?token=x'):
            with self.assertRaises(ValueError):
                FeedbackClient(address)
        self.assertEqual(FeedbackClient('http://127.0.0.1:3006').api_base, 'http://127.0.0.1:3006')


class DiagnosticTests(unittest.TestCase):
    def test_credentials_signed_links_and_home_names_are_removed(self):
        source = (
            '403 Client Error: https://user:pass@www.douyin.com/api?msToken=hidden-token&a_bogus=sign\n'
            'Cookie: sessionid=hidden-session; sid_tt=hidden-sid\n'
            '{"deviceToken": "hidden-device"}\n'
            'ttwid=hidden-browser-cookie\n'
            'Authorization: Bearer hidden-bearer\n'
            'Bearer hidden-bare\n'
            r'C:\Users\Alice\Desktop\app.py:42' + '\n'
            '/home/alice/app.py\n普通错误：下载失败\x00'
        )
        cleaned = sanitize_log(source)
        for value in ('hidden-token', 'hidden-session', 'hidden-sid', 'hidden-device', 'hidden-bearer',
                      'hidden-bare', 'hidden-browser-cookie', 'Alice', 'alice', 'user:pass', '\x00'):
            self.assertNotIn(value, cleaned)
        self.assertIn('403 Client Error', cleaned)
        self.assertIn('普通错误：下载失败', cleaned)

    def test_export_is_bounded_utf8_txt_with_metadata_and_recent_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app.log'
            path.write_text('旧记录\n' * 100000 + '最新错误：失败\n', encoding='utf-8')
            attachment = DiagnosticCollector(path).collect(
                software_name='青柠视频下载', software_version='20261010', user_uuid='fixed-uuid', max_bytes=2048)
            text = attachment.data.decode('utf-8')
            self.assertLessEqual(len(attachment.data), 2048)
            self.assertTrue(attachment.filename.endswith('.txt'))
            self.assertIn('fixed-uuid', text)
            self.assertIn('20261010', text)
            self.assertIn('最新错误', text)

    def test_missing_or_unreadable_log_still_produces_nonempty_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = DiagnosticCollector(Path(directory) / 'missing.log')
            attachment = collector.collect(software_name='软件', software_version='1', user_uuid='id')
            self.assertIn('尚无运行日志', attachment.data.decode())
            with patch.object(Path, 'open', side_effect=PermissionError):
                attachment = collector.collect(software_name='软件', software_version='1', user_uuid='id')
            self.assertIn('无法读取', attachment.data.decode())


class FeedbackServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client = Mock()
        self.client.fetch_limits.return_value = FeedbackLimits()
        self.client.submit.return_value = FeedbackReceipt('a' * 32)
        self.identity = Mock(return_value='same-computer-uuid')
        self.service = FeedbackService(self.client, identity_provider=self.identity,
                                       diagnostics=DiagnosticCollector(Path(self.directory.name) / 'app.log'))

    def image(self, name='image.png', size=100):
        path = Path(self.directory.name) / name
        path.write_bytes(b'\x89PNG\r\n\x1a\n' + b'x' * size)
        return path

    def test_submission_always_has_log_and_existing_identity_without_auth_requests(self):
        receipt = self.service.submit(FeedbackDraft('problem', '  下载失败\n详细步骤  ', image_paths=(self.image(),)))
        fields, files = self.client.submit.call_args.args
        self.assertEqual(receipt.feedback_id, 'a' * 32)
        self.assertEqual(fields['user_uuid'], 'same-computer-uuid')
        self.assertEqual(fields['content'], '下载失败\n详细步骤')
        self.assertEqual(set(fields), {'user_uuid', 'software_name', 'software_version', 'feedback_type', 'content', 'wechat'})
        self.assertEqual(len(files), 2)
        self.assertEqual(files[-1].filename, 'recent_diagnostics.txt')
        self.assertIn(b'same-computer-uuid', files[-1].data)

    def test_no_screenshot_still_has_mandatory_log(self):
        self.service.submit(FeedbackDraft('suggestion', '希望增加新功能'))
        self.assertEqual(len(self.client.submit.call_args.args[1]), 1)

    def test_invalid_drafts_and_missing_identity_never_post(self):
        for draft in (FeedbackDraft('other', '内容'), FeedbackDraft('problem', '  '),
                      FeedbackDraft('problem', 'a' * 10001), FeedbackDraft('problem', '内容', wechat='a\nb'),
                      FeedbackDraft('problem', '内容', image_paths=(Path('a.png'),) * 5)):
            with self.subTest(draft=draft):
                with self.assertRaises(FeedbackError):
                    self.service.submit(draft)
        self.identity.return_value = ''
        with self.assertRaises(FeedbackError):
            self.service.submit(FeedbackDraft('problem', '内容'))
        self.client.submit.assert_not_called()

    def test_changed_missing_and_oversized_images_are_revalidated(self):
        path = self.image()
        path.write_bytes(b'not an image')
        with self.assertRaisesRegex(FeedbackError, '图片格式'):
            self.service.submit(FeedbackDraft('problem', '问题', image_paths=(path,)))
        path.unlink()
        with self.assertRaisesRegex(FeedbackError, '无法读取'):
            self.service.submit(FeedbackDraft('problem', '问题', image_paths=(path,)))
        path = self.image(size=1000)
        self.client.fetch_limits.return_value = replace(FeedbackLimits(), max_file_bytes=900)
        with self.assertRaises(FeedbackError):
            self.service.submit(FeedbackDraft('problem', '问题', image_paths=(path,)))
        self.client.submit.assert_not_called()

    def test_combined_limit_includes_reserved_log_and_config_changes(self):
        self.client.fetch_limits.return_value = replace(FeedbackLimits(), max_total_bytes=67000)
        with self.assertRaisesRegex(FeedbackError, '日志保留空间'):
            self.service.submit(FeedbackDraft('problem', '问题', image_paths=(self.image(size=2000),)))
        self.client.fetch_limits.return_value = replace(FeedbackLimits(), max_files=1)
        with self.assertRaisesRegex(FeedbackError, '最多添加 0 张'):
            self.service.submit(FeedbackDraft('problem', '问题', image_paths=(self.image(),)))
        self.client.submit.assert_not_called()

    def test_config_outage_uses_last_limits_but_missing_log_support_blocks(self):
        self.client.fetch_limits.side_effect = FeedbackError('offline')
        self.service.submit(FeedbackDraft('problem', '内容'))
        self.assertEqual(self.client.submit.call_count, 1)
        self.client.fetch_limits.side_effect = None
        self.client.fetch_limits.return_value = replace(FeedbackLimits(), extensions=('.png',))
        with self.assertRaisesRegex(FeedbackError, '不支持诊断日志'):
            self.service.submit(FeedbackDraft('problem', '内容'))
        self.assertEqual(self.client.submit.call_count, 1)


class LocalFeedbackHTTPTests(unittest.TestCase):
    def test_real_multipart_roundtrip_to_local_server(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, data):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.reply(200, asdict(FeedbackLimits()))

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                message = BytesParser(policy=default).parsebytes(
                    f'Content-Type: {self.headers["Content-Type"]}\r\nMIME-Version: 1.0\r\n\r\n'.encode() + body)
                received.extend((part.get_param('name', header='content-disposition'),
                                 part.get_filename(), part.get_payload(decode=True))
                                for part in message.iter_parts())
                self.reply(202, {'ok': True, 'feedback_id': 'b' * 32})

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / '截图.png'
                path.write_bytes(b'\x89PNG\r\n\x1a\nlocal-test')
                client = FeedbackClient(f'http://127.0.0.1:{server.server_port}')
                service = FeedbackService(client, identity_provider=lambda: 'fixed-id',
                                          diagnostics=DiagnosticCollector(Path(directory) / 'missing.log'))
                receipt = service.submit(FeedbackDraft('problem', '本机测试：下载错误', image_paths=(path,)))
            self.assertEqual(receipt.feedback_id, 'b' * 32)
            files = [item for item in received if item[0] == 'attachments']
            self.assertEqual([item[1] for item in files], ['截图.png', 'recent_diagnostics.txt'])
            self.assertIn('本机测试：下载错误'.encode(), [item[2] for item in received])
            self.assertIn('fixed-id'.encode(), [item[2] for item in received])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
