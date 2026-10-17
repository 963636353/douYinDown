"""原项目四个客户端接口的独立封装，不包含支付商户密钥。"""

from urllib.parse import quote

import requests

from .config import PaymentConfig


class PaymentError(RuntimeError):
    def __init__(self, message, *, kind="response"):
        super().__init__(message)
        self.kind = kind


class PaymentAPI:
    def __init__(self, config: PaymentConfig, identity=None):
        self.config = config
        self.base = config.api_base.rstrip("/")
        self.identity = identity

    def _ensure_registered(self):
        if self.identity is None:
            return
        with self.identity.lock:
            if self.identity.registered:
                return
            payload = {"deviceId": self.identity.computer_uuid}
            if self.identity.legacy_device_id:
                payload["legacyDeviceId"] = self.identity.legacy_device_id
            data = self._object(self._request("POST", "/register", timeout=self.config.request_timeout,
                                             payload=payload))
            if data.get("deviceId") != self.identity.computer_uuid:
                raise PaymentError("设备登记响应不匹配")
            self.identity.registered = True

    def startup(self):
        if self.identity is None:
            raise PaymentError("启动上报需要本机身份")
        self._ensure_registered()
        with self.identity.startup_lock:
            if self.identity.startup_reported:
                return
            data = self._object(self._request("POST", "/startup", timeout=self.config.request_timeout,
                               payload={"uuid": self.identity.computer_uuid}))
            if data.get("uuid") != self.identity.computer_uuid:
                raise PaymentError("启动上报响应身份不匹配")
            self.identity.bind(data["uuid"])
            self.identity.startup_reported = True
            return data

    def _ensure_startup(self):
        if self.identity is not None and self.identity.target_device_id:
            self.startup()

    def _request(self, method, path, *, timeout, payload=None):
        kwargs = {"timeout": timeout, "headers": {}}
        if self.identity is not None:
            kwargs["headers"]["X-Device-UUID"] = self.identity.computer_uuid
        if self.identity is not None and path not in {"/plans", "/register"}:
            self._ensure_registered()
            if path != "/startup":
                self._ensure_startup()
        if payload is not None:
            kwargs["json"] = payload
        try:
            response = requests.request(method, self.base + path, **kwargs)
            response.raise_for_status()
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise PaymentError("无法连接支付/授权服务，请检查网络后重试", kind="network") from exc
        except requests.RequestException as exc:
            message = "支付/授权服务请求失败"
            response = getattr(exc, "response", None)
            if response is not None:
                try:
                    data = response.json()
                    detail = data.get("message") or data.get("detail")
                    if isinstance(detail, str):
                        message = detail
                except (ValueError, AttributeError):
                    pass
            raise PaymentError(message, kind="http") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise PaymentError("服务返回的内容不是有效 JSON") from exc

    @staticmethod
    def _object(data):
        if not isinstance(data, dict):
            raise PaymentError("服务响应应为 JSON 对象")
        return data

    def get_plans(self):
        data = self._request("GET", "/plans", timeout=self.config.request_timeout)
        if not isinstance(data, list):
            raise PaymentError("套餐响应应为 JSON 数组")
        if any(not isinstance(p, dict) or not p.get("planId") for p in data):
            raise PaymentError("套餐信息缺少 planId")
        return data

    def get_license(self, device_id):
        self._ensure_startup()
        device_id = self.identity.device_id if self.identity is not None else device_id
        data = self._object(self._request(
            "GET", "/license/" + quote(str(device_id), safe=""),
            timeout=self.config.request_timeout,
        ))
        if not isinstance(data.get("valid"), bool):
            raise PaymentError("授权响应缺少布尔值 valid")
        return data

    def create_order(self, device_id, plan_id):
        self._ensure_startup()
        device_id = self.identity.device_id if self.identity is not None else device_id
        data = self._object(self._request(
            "POST", "/create-order", timeout=self.config.create_timeout,
            payload={"deviceId": str(device_id), "planId": plan_id},
        ))
        if data.get("success") is not True:
            raise PaymentError(str(data.get("message") or "创建订单失败"))
        if not data.get("orderId") or not isinstance(data.get("qrcode"), str) or not data["qrcode"]:
            raise PaymentError("订单响应缺少二维码或订单号")
        return data

    def get_order_status(self, order_id):
        data = self._object(self._request(
            "GET", "/order-status/" + quote(str(order_id), safe=""),
            timeout=self.config.status_timeout,
        ))
        if not isinstance(data.get("status"), str) or not data["status"]:
            raise PaymentError("订单响应缺少 status")
        return data

    def report_downloads(self, device_uuid, count):
        data = self._object(self._request("POST", "/download-count", timeout=self.config.request_timeout,
                           payload={"uuid": device_uuid, "count": count}))
        if data.get("uuid") != device_uuid or type(data.get("downloaded_video_count")) is not int:
            raise PaymentError("下载上报响应不匹配")
        return data
