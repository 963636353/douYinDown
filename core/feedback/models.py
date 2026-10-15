"""Feedback protocol values and validation shared by the client and GUI."""

from dataclasses import dataclass
from pathlib import Path


DIAGNOSTIC_MAX_BYTES = 64 * 1024
REQUEST_MAX_BYTES = 16 * 1024 * 1024
IMAGE_MIME_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png'}


class FeedbackError(Exception):
    """User-readable failure; ambiguous results must never trigger automatic retry."""

    def __init__(self, message: str, *, retry_after: int = 0, result_unknown: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.result_unknown = result_unknown


@dataclass(frozen=True)
class FeedbackLimits:
    max_files: int = 5
    max_file_bytes: int = 10 * 1024 * 1024
    max_total_bytes: int = 15 * 1024 * 1024
    max_content_length: int = 10000
    extensions: tuple[str, ...] = ('.jpg', '.jpeg', '.png', '.txt')
    uuid_min_interval_seconds: int = 60
    uuid_daily_limit: int = 5

    @classmethod
    def from_json(cls, data: dict) -> 'FeedbackLimits':
        if not isinstance(data, dict):
            raise FeedbackError('反馈服务返回的上传规则格式不正确。')
        names = ('max_files', 'max_file_bytes', 'max_total_bytes', 'max_content_length',
                 'uuid_min_interval_seconds', 'uuid_daily_limit')
        if any(type(data.get(name)) is not int or data[name] < 1 for name in names):
            raise FeedbackError('反馈服务返回的上传规则格式不正确。')
        extensions = data.get('extensions')
        if not isinstance(extensions, list) or not extensions or any(
            not isinstance(item, str) or not item.startswith('.') for item in extensions
        ):
            raise FeedbackError('反馈服务返回的附件类型格式不正确。')
        return cls(**{name: data[name] for name in names},
                   extensions=tuple(item.lower() for item in extensions))

    @property
    def max_images(self) -> int:
        return self.max_files - 1

    @property
    def log_budget(self) -> int:
        return min(DIAGNOSTIC_MAX_BYTES, self.max_file_bytes, self.max_total_bytes)

    @property
    def image_budget(self) -> int:
        # Always reserve space as well as a file slot for the mandatory log.
        return min(self.max_total_bytes, REQUEST_MAX_BYTES) - self.log_budget


@dataclass(frozen=True)
class FeedbackDraft:
    feedback_type: str
    content: str
    wechat: str = ''
    image_paths: tuple[Path, ...] = ()

    def validate(self, limits: FeedbackLimits) -> None:
        if self.feedback_type not in ('problem', 'suggestion'):
            raise FeedbackError('请选择问题反馈或使用建议。')
        if not self.content.strip():
            raise FeedbackError('请描述遇到的问题或者建议，方便工作人员定位问题。')
        if len(self.content.strip()) > limits.max_content_length:
            raise FeedbackError(f'详细描述最多填写 {limits.max_content_length} 个字符，请适当精简。')
        if len(self.wechat.strip()) > 100 or any(char in self.wechat for char in '\r\n'):
            raise FeedbackError('微信号不能超过 100 个字符，也不能包含换行。')
        if len(self.image_paths) > limits.max_images:
            raise FeedbackError(f'附带诊断日志时，最多添加 {limits.max_images} 张截图，请先移除多余截图。')


@dataclass(frozen=True)
class FeedbackAttachment:
    filename: str
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class FeedbackReceipt:
    feedback_id: str


def single_line(value: str, name: str, max_length: int, *, required: bool = True) -> str:
    value = value.strip()
    if (required and not value) or len(value) > max_length or any(char in value for char in '\r\n'):
        raise FeedbackError(f'{name}缺失或格式不正确，请稍后重试。')
    return value
