import time
import uuid
from datetime import datetime, timezone
from threading import RLock

from .api import PaymentError


def get_device_id():
    """沿用原项目算法，已有用户迁移时不要直接改变此标识。"""
    return str(uuid.getnode())


class LicenseService:
    """可在其他界面框架中复用的授权状态；服务端仍是授权事实来源。"""

    def __init__(self, api, config, device_id=None):
        self.api = api
        self.config = config
        self.device_id = str(device_id) if device_id is not None else get_device_id()
        if not self.device_id:
            raise ValueError("device_id 不能为空")
        self.latest_license = {"valid": False}
        self._last_verified = None
        self._verified_license = None
        self._refresh_lock = RLock()

    def _can_use_grace(self):
        if self._last_verified is None or not self._verified_license:
            return False
        if time.monotonic() - self._last_verified >= self.config.network_grace_seconds:
            return False
        # 无明确时区或无到期时间时，不猜测缓存的有效期。
        try:
            expiry = datetime.fromisoformat(
                str(self._verified_license["expireTime"]).replace("Z", "+00:00")
            )
            return expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)
        except (KeyError, ValueError, TypeError):
            return False

    def refresh_license(self):
        with self._refresh_lock:
            return self._refresh_license()

    def _refresh_license(self):
        try:
            data = self.api.get_license(self.device_id)
            identity = getattr(self.api, "identity", None)
            if identity is not None:
                self.device_id = identity.device_id
            if not isinstance(data, dict) or not isinstance(data.get("valid"), bool):
                raise PaymentError("授权响应格式错误")
            self.latest_license = dict(data)
            self.latest_license.pop("error", None)
            self.latest_license.pop("cached", None)
            self._verified_license = dict(self.latest_license) if data["valid"] else None
            self._last_verified = time.monotonic() if data["valid"] else None
        except PaymentError as exc:
            if exc.kind == "network" and self._can_use_grace():
                self.latest_license = dict(self._verified_license, error="network", cached=True)
            else:
                self.latest_license = {"valid": False, "error": exc.kind, "message": str(exc)}
                if exc.kind != "network":
                    self._verified_license = None
                    self._last_verified = None
        return self.latest_license["valid"]
