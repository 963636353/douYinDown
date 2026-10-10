import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


SERVER_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Plan:
    name: str
    amount_cents: int
    days: int


# 修改价格时使用整数“分”；已创建订单保留原金额和天数。
PLANS = {
    "plan_30d": Plan("30 天授权", 1000, 30),
    "plan_365d": Plan("365 天授权", 6800, 365),
    "plan_999d": Plan("999 天授权", 12800, 999),
}


@dataclass(frozen=True)
class Settings:
    database_url: str = f"sqlite:///{(SERVER_DIR / 'payment.db').as_posix()}"
    mode: str = "wechat"
    app_id: str = ""
    merchant_id: str = ""
    api_key: str = field(default="", repr=False)
    notify_url: str = ""
    trial_days: int = 3
    order_minutes: int = 30
    query_interval_seconds: int = 15
    product_name: str = "青柠视频下载"

    def __post_init__(self):
        if self.mode not in {"wechat", "demo"}:
            raise ValueError("PAYMENT_MODE 仅支持 wechat 或 demo")
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("第一版使用独立 SQLite 数据库")
        if not 0 <= self.trial_days <= 365 or not 5 <= self.order_minutes <= 120:
            raise ValueError("试用天数或订单有效期超出范围")

    @classmethod
    def from_env(cls):
        load_dotenv(SERVER_DIR / ".env", override=False)
        mode = os.getenv("PAYMENT_MODE", "wechat")
        database = "payment-demo.db" if mode == "demo" else "payment.db"
        return cls(
            database_url=os.getenv("PAYMENT_DATABASE_URL", f"sqlite:///{(SERVER_DIR / database).as_posix()}"),
            mode=mode,
            app_id=os.getenv("WX_APP_ID", ""),
            merchant_id=os.getenv("WX_MCH_ID", ""),
            api_key=os.getenv("WX_API_V2_KEY", ""),
            notify_url=os.getenv("WX_NOTIFY_URL", ""),
            trial_days=int(os.getenv("PAYMENT_TRIAL_DAYS", "3")),
        )

    def validate_merchant(self):
        if not self.app_id or not self.merchant_id or len(self.api_key.encode("utf-8")) != 32:
            raise ValueError("请在服务端配置 AppID、商户号及 32 字节 APIv2 密钥")
        address = urlsplit(self.notify_url)
        if address.scheme != "https" or not address.netloc or address.query or address.fragment:
            raise ValueError("WX_NOTIFY_URL 必须是公网 HTTPS 回调地址且不能带参数")
