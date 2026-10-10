from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PaymentConfig:
    # 必须显式配置新产品的服务地址，不默认连接原软件的正式收款服务。
    api_base: str
    product_name: str = "我的软件"
    payment_hint: str = "请使用微信扫码完成支付"
    paid_message: str = "支付成功，授权已生效"
    request_timeout: float = 8
    create_timeout: float = 12
    status_timeout: float = 5
    poll_interval: float = 3
    order_wait_seconds: float = 600
    # 默认每次在线验证。若启用宽限，只在当前进程内生效，并受到期时间约束。
    network_grace_seconds: float = 0

    def __post_init__(self):
        if not isinstance(self.api_base, str):
            raise ValueError("api_base 必须是字符串")
        parts = urlsplit(self.api_base)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("api_base 必须是完整的 HTTP/HTTPS 接口地址")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("api_base 不能包含查询参数、片段或用户名密码")
        for name in ("request_timeout", "create_timeout", "status_timeout",
                     "poll_interval", "order_wait_seconds"):
            if not 0 < getattr(self, name) < float("inf"):
                raise ValueError(f"{name} 必须是有限正数")
        if not 0 <= self.network_grace_seconds < float("inf"):
            raise ValueError("network_grace_seconds 必须是有限非负数")
