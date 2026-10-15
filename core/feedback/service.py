"""Prepare a complete submission using existing computer identity and log storage."""

import re
from pathlib import Path

from core.paths import runtime_path
from core.update_checker import APP_VERSION

from .client import FeedbackClient
from .diagnostics import DiagnosticCollector
from .models import (
    IMAGE_MIME_TYPES, FeedbackAttachment, FeedbackDraft, FeedbackError, FeedbackLimits,
    FeedbackReceipt, single_line,
)


def existing_computer_uuid() -> str:
    # Same identity and location as authorization; feedback never registers a device
    # or requires successful payment-service connectivity.
    from core.payment_config import client_data_dir
    from payment_kit.computer import ComputerIdentity

    try:
        return ComputerIdentity(client_data_dir() / 'computer.json').uuid
    except (OSError, ValueError, TypeError, KeyError):
        raise FeedbackError('暂时无法读取本机电脑标识，请稍后重试或联系作者。') from None


def image_attachment(path: Path, limits: FeedbackLimits) -> FeedbackAttachment:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in IMAGE_MIME_TYPES or suffix not in limits.extensions:
        raise FeedbackError(f'{path.name}：当前服务不支持该截图类型。')
    try:
        # Read a bounded snapshot so a changed file cannot grow the upload unnoticed.
        with path.open('rb') as stream:
            data = stream.read(min(limits.max_file_bytes, limits.image_budget) + 1)
    except OSError:
        raise FeedbackError(f'{path.name}：图片无法读取，请重新添加。') from None
    if not data or len(data) > limits.max_file_bytes:
        raise FeedbackError(f'{path.name}：图片不能为空，且不能超过单文件大小限制。')
    if len(data) > limits.image_budget:
        raise FeedbackError('截图合计过大，请减少截图大小，为诊断日志保留空间。')
    signature = b'\x89PNG\r\n\x1a\n' if suffix == '.png' else b'\xff\xd8\xff'
    if not data.startswith(signature):
        raise FeedbackError(f'{path.name}：图片格式已变化，请重新添加有效截图。')
    stem = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', '_', path.stem)
    filename = stem[:150 - len(suffix)] + suffix
    return FeedbackAttachment(filename, data, IMAGE_MIME_TYPES[suffix])


class FeedbackService:
    def __init__(self, client=None, *, identity_provider=None, diagnostics=None,
                 software_name='青柠视频下载', software_version=APP_VERSION):
        self.client = client or FeedbackClient()
        self.identity_provider = identity_provider or existing_computer_uuid
        self.diagnostics = diagnostics or DiagnosticCollector(Path(runtime_path('logs', 'app.log')))
        self.software_name = single_line(software_name, '软件名称', 100)
        self.software_version = single_line(software_version, '软件版本', 50)
        self.limits = FeedbackLimits()

    def fetch_limits(self) -> FeedbackLimits:
        limits = self.client.fetch_limits()
        self.limits = limits
        return limits

    def submit(self, draft: FeedbackDraft) -> FeedbackReceipt:
        try:
            limits = self.fetch_limits()
        except FeedbackError:
            limits = self.limits  # A config outage must not prevent a valid POST.
        if '.txt' not in limits.extensions:
            raise FeedbackError('反馈服务暂不支持诊断日志附件，请联系作者。')
        draft.validate(limits)
        user_uuid = single_line(self.identity_provider(), '电脑标识', 128)
        images = []
        total = 0
        for path in draft.image_paths:
            image = image_attachment(path, limits)
            total += len(image.data)
            if total > limits.image_budget:
                raise FeedbackError('截图合计过大，请减少截图大小，为诊断日志保留空间。')
            images.append(image)
        log = self.diagnostics.collect(software_name=self.software_name, software_version=self.software_version,
                                       user_uuid=user_uuid, max_bytes=limits.log_budget)
        attachments = (*images, log)
        if (len(attachments) > limits.max_files or any(not item.data or len(item.data) > limits.max_file_bytes
                                                     for item in attachments)
                or sum(len(item.data) for item in attachments) > limits.max_total_bytes):
            raise FeedbackError('附件超过当前上传限制，请减少截图大小或数量。')
        return self.client.submit({
            'user_uuid': user_uuid, 'software_name': self.software_name,
            'software_version': self.software_version, 'feedback_type': draft.feedback_type,
            'content': draft.content.strip(), 'wechat': draft.wechat.strip(),
        }, attachments)
