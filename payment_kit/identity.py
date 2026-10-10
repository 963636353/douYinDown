"""本地安装标识和设备令牌；不同服务地址使用独立凭据文件。"""

import json
import os
import secrets
import time
import uuid
from pathlib import Path
from threading import Lock


class DeviceIdentity:
    def __init__(self, path, api_base, *, device_id=None, legacy_path=None):
        self.path = Path(path)
        self.api_base = api_base.rstrip("/")
        self.lock = Lock()
        self.startup_lock = Lock()
        self.target_device_id = device_id
        self.startup_reported = False
        self.registered = False
        if self.path.exists():
            self._load()
        else:
            if legacy_path and Path(legacy_path).exists():
                self._load(Path(legacy_path))
            else:
                self.device_id, self.token = device_id or uuid.uuid4().hex, secrets.token_urlsafe(32)
            # 登记前落盘。响应丢失时使用相同令牌重试，不会丢失已登记身份。
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                # 排他创建，两个客户端同时首次启动时不能互相覆盖凭据。
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                self._load()
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                    json.dump({"apiBase": self.api_base, "deviceId": self.device_id,
                               "deviceToken": self.token}, file, ensure_ascii=False, indent=2)

    @property
    def computer_uuid(self):
        return self.target_device_id or self.device_id

    def bind(self, device_id):
        if self.target_device_id and device_id != self.target_device_id:
            raise ValueError("服务器返回的电脑身份不匹配")
        if self.device_id == device_id:
            return
        temporary = self.path.with_name(self.path.name + "." + secrets.token_hex(6) + ".tmp")
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
            json.dump({"apiBase": self.api_base, "deviceId": device_id, "deviceToken": self.token}, handle, indent=2)
        temporary.replace(self.path)
        self.device_id = device_id

    def _load(self, path=None):
        for attempt in range(10):
            try:
                data = json.loads((path or self.path).read_text(encoding="utf-8"))
                if (data["apiBase"] != self.api_base or not isinstance(data["deviceId"], str)
                        or not isinstance(data["deviceToken"], str) or len(data["deviceToken"]) < 43):
                    raise ValueError()
                self.device_id, self.token = data["deviceId"], data["deviceToken"]
                return
            except (ValueError, KeyError, TypeError) as exc:
                if attempt == 9:
                    raise ValueError("本地授权凭据文件损坏，请联系客服恢复；不要随意删除已有付费凭据") from exc
                time.sleep(0.05)
