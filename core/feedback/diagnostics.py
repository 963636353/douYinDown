"""Bounded, text-only diagnostic export; never open cookie or credential files."""

import platform
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .models import DIAGNOSTIC_MAX_BYTES, FeedbackAttachment, FeedbackError


LOG_READ_MAX_BYTES = 256 * 1024
LOG_MAX_LINES = 400
_URL = re.compile(r'https?://[^\s<>\x22\x27]+', re.IGNORECASE)
_SECRET = re.compile(
    r'''(?ix)(?:["']?)(?:cookie|set-cookie|authorization|sessionid\w*|sid_tt|sid_guard|
    \w*token\w*|\w*csrf\w*|uifid\w*|s_v_web_id|verifyfp|password|passwd|secret\w*|
    ttwid|odin_tt|(?:passport|session|sid|ssid|uid|sso)_\w+|
    api[_-]?key|signature|a_bogus|x[-_]secsdk[-_]web[-_](?:signature|expire))
    ["']?\s*[:=]'''
)
_HOME_WINDOWS = re.compile(r'(?i)([a-z]:[\\/](?:Users|Documents and Settings)[\\/])[^\\/\s"\x27]+')
_HOME_UNIX = re.compile(r'(/(?:home|Users)/)[^/\s"\x27]+')
_CONTROLS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def sanitize_log(text: str) -> str:
    def hide_url(match):
        try:
            address = urlsplit(match.group())
            host = address.hostname
        except ValueError:
            host = None
        return f'{address.scheme}://{host}/[地址已隐藏]' if host else '[地址已隐藏]'

    lines = []
    for line in _CONTROLS.sub('', text).splitlines():
        line = _URL.sub(hide_url, line)
        # Drop the entire line rather than guessing where an embedded credential ends.
        if _SECRET.search(line):
            lines.append('[已隐藏包含凭据的日志行]')
            continue
        line = re.sub(r'(?i)\bBearer\s+[a-z0-9._~+/=-]+', 'Bearer [已隐藏]', line)
        line = _HOME_WINDOWS.sub(lambda match: match[1] + '[用户]', line)
        line = _HOME_UNIX.sub(lambda match: match[1] + '[用户]', line)
        lines.append(line)
    return '\n'.join(lines)


class DiagnosticCollector:
    def __init__(self, log_path: Path):
        self.log_path = Path(log_path)

    def _recent_log(self) -> str:
        try:
            with self.log_path.open('rb') as stream:
                stream.seek(0, 2)
                start = max(0, stream.tell() - LOG_READ_MAX_BYTES)
                stream.seek(start)
                data = stream.read(LOG_READ_MAX_BYTES)
            if start:
                data = data.partition(b'\n')[2]
            return sanitize_log('\n'.join(data.decode('utf-8', errors='replace').splitlines()[-LOG_MAX_LINES:]))
        except FileNotFoundError:
            return '尚无运行日志。'
        except OSError:
            return '近期运行日志暂时无法读取。'

    def collect(self, *, software_name: str, software_version: str, user_uuid: str,
                max_bytes: int = DIAGNOSTIC_MAX_BYTES) -> FeedbackAttachment:
        captured_at = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S +08:00')
        header = (
            f'{software_name} - 近期诊断信息\n'
            f'软件版本：{software_version}\n电脑标识：{user_uuid}\n'
            f'生成时间：{captured_at}\n'
            f'系统：{platform.system()} {platform.release()}\n'
            f'Python：{platform.python_version()}\n运行方式：{"打包程序" if getattr(sys, "frozen", False) else "源码运行"}\n'
            '仅包含近期运行记录；链接、用户目录及可识别的凭据字段已过滤。\n\n'
        ).encode('utf-8')
        budget = min(max_bytes, DIAGNOSTIC_MAX_BYTES)
        if budget < len(header) + 128:
            raise FeedbackError('当前附件大小限制不足以附带诊断日志，请稍后重试或联系作者。')
        body = self._recent_log().encode('utf-8')
        if len(header) + len(body) > budget:
            note = '[仅保留最近的日志片段]\n'.encode('utf-8')
            body = note + body[-(budget - len(header) - len(note)):].decode('utf-8', errors='ignore').encode('utf-8')
        return FeedbackAttachment('recent_diagnostics.txt', header + body, 'text/plain')
