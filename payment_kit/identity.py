"""仅使用稳定的电脑 UUID；旧凭据只读，用于关联早期随机设备编号。"""

import json
import re
import uuid
from pathlib import Path
from threading import Lock


class DeviceIdentity:
    def __init__(self, path, api_base, *, device_id, legacy_path=None):
        try:
            computer = uuid.UUID(device_id)
            if computer.version != 5:
                raise ValueError()
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("需要有效的电脑 UUID，不能随机创建新的试用身份") from exc
        self.device_id = self.target_device_id = computer.hex
        self.path = Path(path)
        self.api_base = api_base.rstrip("/")
        self.lock = Lock()
        self.startup_lock = Lock()
        self.startup_reported = False
        self.registered = False
        self.legacy_device_id = None
        # 不创建、覆盖或依赖凭据文件。损坏/丢失的旧文件不影响 UUID 用户使用。
        for candidate in (self.path, Path(legacy_path) if legacy_path else None):
            if candidate is None:
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8-sig"))
                old_id = data.get("deviceId")
                if (data.get("apiBase", "").rstrip("/") != self.api_base
                        or not isinstance(old_id, str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", old_id)):
                    continue
                try:
                    bound = uuid.UUID(old_id).version == 5
                except ValueError:
                    bound = False
                if not bound:
                    self.legacy_device_id = old_id
                    break
            except (OSError, ValueError, AttributeError, TypeError):
                continue

    @property
    def computer_uuid(self):
        return self.device_id

    def bind(self, device_id):
        if device_id != self.device_id:
            raise ValueError("服务器返回的电脑身份不匹配")
