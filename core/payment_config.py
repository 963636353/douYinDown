"""客户端仅保存服务地址和设备凭据，不保存微信商户参数。"""

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from core.paths import runtime_path
from payment_kit import PaymentConfig


DEFAULT_API_BASE = "https://douyindown.secs.cc/api/pc"


def load_payment_config():
    data = {}
    path = runtime_path("payment_client.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("payment_client.json 应为 JSON 对象")
    config = PaymentConfig(
        api_base=os.getenv("QINGNING_PAYMENT_API_BASE") or data.get("api_base", DEFAULT_API_BASE),
        product_name="青柠视频下载",
    )
    address = urlsplit(config.api_base)
    if address.scheme == "http" and address.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("正式支付服务必须使用 HTTPS；HTTP 仅供本机调试")
    return config


def client_data_dir():
    base = os.getenv("LOCALAPPDATA") if os.name == "nt" else None
    return Path(base or (Path.home() / ".local" / "share")) / "QingningVideoDownloader"


def service_key(api_base):
    return hashlib.sha256(api_base.rstrip("/").encode("utf-8")).hexdigest()[:16]


def credentials_path(api_base):
    return str(client_data_dir() / "payment_credentials" / (service_key(api_base) + ".json"))


def legacy_credentials_path(api_base):
    name = hashlib.sha256(api_base.rstrip("/").encode("utf-8")).hexdigest()[:16]
    return runtime_path("payment_credentials", name + ".json")
