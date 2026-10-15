"""HTTPS feedback protocol. No authentication, redirects, or automatic retries."""

import os
import re
from urllib.parse import urlsplit

import requests

from .models import (
    REQUEST_MAX_BYTES, FeedbackAttachment, FeedbackError, FeedbackLimits, FeedbackReceipt,
)


DEFAULT_API_BASE = 'https://bug.secs.cc'


class FeedbackClient:
    def __init__(self, api_base: str | None = None, *, http=None):
        self.api_base = (api_base or os.getenv('QINGNING_FEEDBACK_API_BASE') or DEFAULT_API_BASE).rstrip('/')
        address = urlsplit(self.api_base)
        local_http = address.scheme == 'http' and address.hostname in ('127.0.0.1', 'localhost', '::1')
        if (address.scheme != 'https' and not local_http) or not address.hostname or (
            address.username or address.password or address.query or address.fragment
        ):
            raise ValueError('反馈服务地址须使用 HTTPS，HTTP 仅供本机调试，且不能包含账号或查询参数。')
        self._http = http or requests

    @staticmethod
    def _json(response):
        try:
            return response.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            return None

    @staticmethod
    def _retry_after(response, data) -> int:
        values = [response.headers.get('Retry-After')]
        if isinstance(data, dict):
            values.append(data.get('retry_after'))
        seconds = []
        for value in values:
            if isinstance(value, bool):
                continue
            if re.fullmatch(r'[0-9]{1,9}', str(value or '')):
                seconds.append(int(value))
        return max([1, *seconds])

    def fetch_limits(self) -> FeedbackLimits:
        try:
            response = self._http.get(f'{self.api_base}/api/config', timeout=(5, 5), allow_redirects=False)
        except requests.RequestException:
            raise FeedbackError('暂时无法获取反馈服务的上传规则。') from None
        try:
            if response.status_code != 200:
                raise FeedbackError(f'暂时无法获取上传规则（HTTP {response.status_code}）。')
            return FeedbackLimits.from_json(self._json(response))
        finally:
            response.close()

    def submit(self, fields: dict[str, str], attachments: tuple[FeedbackAttachment, ...]) -> FeedbackReceipt:
        # (None, value) forces multipart even if a future caller has no files.
        parts = [(name, (None, value)) for name, value in fields.items()]
        parts.extend(('attachments', (item.filename, item.data, item.mime_type)) for item in attachments)
        request = requests.Request('POST', f'{self.api_base}/api/feedback', files=parts).prepare()
        if int(request.headers['Content-Length']) > REQUEST_MAX_BYTES:
            raise FeedbackError('反馈内容连同附件超过请求大小限制，请减少截图大小。')
        try:
            response = self._http.post(request.url, data=request.body, headers=dict(request.headers),
                                       timeout=(10, 90), allow_redirects=False)
        except requests.RequestException:
            raise FeedbackError(
                '网络异常，提交结果未确认，服务端可能已经收到反馈。内容已保留，请避免重复提交。',
                result_unknown=True,
            ) from None
        try:
            data = self._json(response)
            status = response.status_code
            if status == 202:
                if (isinstance(data, dict) and data.get('ok') is True
                        and isinstance(data.get('feedback_id'), str)
                        and re.fullmatch(r'[0-9a-f]{32}', data['feedback_id'])):
                    return FeedbackReceipt(data['feedback_id'])
                raise FeedbackError('服务响应异常，提交结果未确认。内容已保留，请避免重复提交。', result_unknown=True)
            if status == 429:
                wait = self._retry_after(response, data)
                raise FeedbackError(f'提交过于频繁，请等待 {wait} 秒后再试；每日额度也可能已用完。', retry_after=wait)
            if status >= 500 and status != 503:
                raise FeedbackError(f'服务响应异常（HTTP {status}），提交结果未确认。内容已保留，请避免重复提交。',
                                    result_unknown=True)
            fallback = {
                413: '附件或反馈内容过大，请减少截图大小或数量。',
                422: '反馈内容或附件不符合要求，请检查后再提交。',
                503: '反馈服务暂时繁忙，请稍后再试。',
            }.get(status, f'提交未成功（HTTP {status}），请稍后再试。')
            message = data.get('message') if isinstance(data, dict) else None
            if isinstance(message, str) and message.strip():
                fallback = message.strip()[:500]
            raise FeedbackError(fallback)
        finally:
            response.close()
