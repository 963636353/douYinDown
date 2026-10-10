"""支付和设备授权复用包；核心接口不依赖 Qt，界面按需导入。"""

from .api import PaymentAPI, PaymentError
from .config import PaymentConfig
from .license import LicenseService, get_device_id

__all__ = ["PaymentAPI", "PaymentError", "PaymentConfig", "LicenseService", "get_device_id"]
